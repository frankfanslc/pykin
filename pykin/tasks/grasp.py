import numpy as np
from enum import Enum, auto
from collections import OrderedDict
from copy import deepcopy

from pykin.tasks.activity import ActivityBase
from pykin.utils.task_utils import normalize, surface_sampling, projection, get_rotation_from_vectors, get_relative_transform
from pykin.utils.transform_utils import get_pose_from_homogeneous
from pykin.utils.log_utils import create_logger

logger = create_logger('Grasp', "debug")

class GraspStatus(Enum):
    pre_grasp_pose = auto()
    grasp_pose = auto()
    post_grasp_pose = auto()
    pre_release_pose = auto()
    release_pose = auto()
    post_release_pose = auto()

class GraspManager(ActivityBase):
    def __init__(
        self,
        robot,
        robot_col_manager,
        objects_col_manager,
        mesh_path,
        retreat_distance = 0.1,
        release_distance = 0.01,
        **gripper_configures
    ):
        super().__init__(
            robot,
            robot_col_manager,
            objects_col_manager,
            mesh_path,
            **gripper_configures)

        self.retreat_distance = retreat_distance
        self.release_distance = release_distance
        self.tcp_pose = np.eye(4)
        self.result_obj_pose = np.eye(4)
        self.contact_points = None
        self.result_object_c_manager = []
        self.result_object_c_manager.append(self.objects_c_manager)
        self.obj_info = None

    def get_grasp_waypoints(
        self,
        obj_info=None,
        obj_mesh=None,
        obj_pose=None,
        limit_angle=0.1,
        num_grasp=1,
        n_trials=1,
    ):
        waypoints = OrderedDict()
        if obj_info:
            self.obj_info = obj_info
            obj_mesh = obj_info["gtype"]
            obj_pose = obj_info["transform"]

        grasp_pose = self.get_grasp_pose(obj_mesh, obj_pose, limit_angle, num_grasp, n_trials)    
        waypoints[GraspStatus.pre_grasp_pose] = self.pre_grasp_pose
        waypoints[GraspStatus.grasp_pose] = grasp_pose
        waypoints[GraspStatus.post_grasp_pose] =self.post_grasp_pose

        return waypoints
        
    def get_grasp_pose(        
        self,
        obj_mesh,
        obj_pose,
        limit_angle,
        num_grasp=1,
        n_trials=1,
    ):
        grasp_poses = self.generate_grasps(obj_mesh, obj_pose, limit_angle, num_grasp, n_trials)
        grasp_pose = self.filter_grasps(grasp_poses)     
        return grasp_pose

    def get_pre_grasp_pose(self, grasp_pose):
        pre_grasp_pose = np.eye(4)
        pre_grasp_pose[:3, :3] = grasp_pose[:3, :3]
        pre_grasp_pose[:3, 3] = grasp_pose[:3, 3] - self.retreat_distance * grasp_pose[:3,2]    
        # pre_grasp_pose[:3, 3] = grasp_pose[:3, 3] + np.array([0, 0, self.retreat_distance])  
        return pre_grasp_pose

    def get_post_grasp_pose(self, grasp_pose):
        post_grasp_pose = np.eye(4)
        post_grasp_pose[:3, :3] = grasp_pose[:3, :3] 
        post_grasp_pose[:3, 3] = grasp_pose[:3, 3] - self.retreat_distance * grasp_pose[:3,2] 
        # post_grasp_pose[:3, 3] = grasp_pose[:3, 3] + np.array([0, 0, self.retreat_distance])  
        return post_grasp_pose

    def generate_grasps(
        self,
        obj_mesh,
        obj_pose,
        limit_angle=0.05, #radian
        num_grasp=1,
        n_trials=1
    ):
        cnt = 0
        while cnt < num_grasp * n_trials:
            tcp_poses = self.generate_tcp_poses(obj_mesh, obj_pose, limit_angle, n_trials)
            for tcp_pose, contact_points, _ in tcp_poses:
                gripper_transformed = self.get_gripper_transformed(tcp_pose)

                if self.collision_free(gripper_transformed, only_gripper=True):
                    self.tcp_pose = tcp_pose
                    self.contact_points = contact_points
                    eef_pose = self.get_eef_h_mat_from_tcp(tcp_pose)
                    cnt += 1
                    yield (eef_pose, gripper_transformed)
    
    def filter_grasps(self, grasp_poses):
        is_success_filtered = False
        for grasp_pose, _ in grasp_poses:
            qpos = self._compute_inverse_kinematics(grasp_pose)
            if qpos is None:
                continue

            transforms = self.robot.forward_kin(np.array(qpos))
            goal_pose = transforms[self.robot.eef_name].h_mat
 
            if self._check_ik_solution(grasp_pose, goal_pose) and self.collision_free(transforms):
                pre_grasp_pose = self.get_pre_grasp_pose(grasp_pose)
                pre_qpos = self._compute_inverse_kinematics(pre_grasp_pose)
                pre_transforms = self.robot.forward_kin(np.array(pre_qpos))
                pre_goal_pose = pre_transforms[self.robot.eef_name].h_mat

                if self._check_ik_solution(pre_grasp_pose, pre_goal_pose) and self.collision_free(pre_transforms):
                    self.pre_grasp_pose = pre_grasp_pose
                    
                    post_grasp_pose = self.get_post_grasp_pose(grasp_pose)
                    post_qpos = self._compute_inverse_kinematics(post_grasp_pose)
                    post_transforms = self.robot.forward_kin(np.array(post_qpos))
                    post_goal_pose = post_transforms[self.robot.eef_name].h_mat
                    
                    if self.obj_info:
                        # Attach gripper to object
                        self.T_between_gripper_and_obj = get_relative_transform(grasp_pose, self.obj_info["transform"])
                        obj_post_grasp_pose = np.dot(post_grasp_pose, self.T_between_gripper_and_obj)

                        self.robot_c_manager.add_object(
                            self.obj_info["name"], 
                            gtype="mesh", gparam=self.obj_info["gtype"], transform=obj_post_grasp_pose)
                        self.obj_grasp_pose = self.obj_info["transform"]
                        self.obj_post_grasp_pose = obj_post_grasp_pose
                    
                    if self._check_ik_solution(post_grasp_pose, post_goal_pose) and self.collision_free(post_transforms):
                        self.post_grasp_pose = post_grasp_pose
                        
                        is_success_filtered = True
                        break

        if not is_success_filtered:
            logger.error(f"Failed to filter grasp poses")
            return None

        # self.robot_c_manager.remove_object(self.obj_info["name"])
        logger.info(f"Success to get grasp pose.\n")

        return grasp_pose

    def generate_tcp_poses(
        self,
        obj_mesh,
        obj_pose,
        limit_angle,
        n_trials
    ):
        contact_points, normals = self._generate_contact_points(obj_mesh, obj_pose, limit_angle)
        p1, p2 = contact_points
        center_point = (p1 + p2) /2
        line = p2 - p1

        for i, grasp_dir in enumerate(self._generate_grasp_directions(line, n_trials)):
            y = normalize(line)
            z = grasp_dir
            x = np.cross(y, z)

            tcp_pose = np.eye(4)
            tcp_pose[:3,0] = x
            tcp_pose[:3,1] = y
            tcp_pose[:3,2] = z
            tcp_pose[:3,3] = center_point

            yield (tcp_pose, contact_points, normals)

    def get_release_waypoints(
        self,
        obj_info_on_sup=None,
        n_samples_on_sup=1,
        obj_info_for_sup=None,
        n_samples_for_sup=1,
        n_trials=1,
        obj_mesh_on_sup=None,
        obj_pose_on_sup=None,
        obj_mesh_for_sup=None,
        obj_pose_for_sup=None,
    ):
        waypoints = OrderedDict()

        if obj_info_on_sup:
            obj_mesh_on_sup = obj_info_on_sup["gtype"]
            obj_pose_on_sup = obj_info_on_sup["transform"]

        if obj_info_for_sup:
            obj_mesh_for_sup = obj_info_for_sup["gtype"]
            obj_pose_for_sup = obj_info_for_sup["transform"]

        release_pose = self.get_release_pose(
            obj_mesh_on_sup,
            obj_pose_on_sup,
            n_samples_on_sup,
            obj_mesh_for_sup,
            obj_pose_for_sup,
            n_samples_for_sup, 
            n_trials)
        
        waypoints[GraspStatus.pre_release_pose] = self.pre_release_pose
        waypoints[GraspStatus.release_pose] = release_pose
        waypoints[GraspStatus.post_release_pose] =self.post_release_pose

        return waypoints
        
    def get_release_pose(
        self,
        obj_mesh_on_sup,
        obj_pose_on_sup,
        n_samples_on_sup,
        obj_mesh_for_sup,
        obj_pose_for_sup,
        n_samples_for_sup,
        n_trials
    ):
        support_poses = self.generate_supports(
            obj_mesh_on_sup,
            obj_pose_on_sup,
            n_samples_on_sup,
            obj_mesh_for_sup,
            obj_pose_for_sup,
            n_samples_for_sup,
            n_trials)

        release_pose = self.filter_supports(support_poses)
        return release_pose

    def get_pre_release_pose(self, release_pose):
        pre_release_pose = np.eye(4)
        pre_release_pose[:3, :3] = release_pose[:3, :3]
        pre_release_pose[:3, 3] = release_pose[:3, 3] + np.array([0, 0, self.retreat_distance])
        return pre_release_pose

    def get_post_release_pose(self, grasp_pose):
        post_release_pose = np.eye(4)
        post_release_pose[:3, :3] = grasp_pose[:3, :3] 
        post_release_pose[:3, 3] = grasp_pose[:3, 3] - self.retreat_distance * grasp_pose[:3,2]
        return post_release_pose

    def generate_supports(
        self,
        obj_mesh_on_sup,
        obj_pose_on_sup,
        n_samples_on_sup,
        obj_mesh_for_sup,
        obj_pose_for_sup,
        n_samples_for_sup,
        n_trials=1
    ):
        cnt = 0
        self.obj_mesh_for_sup = deepcopy(obj_mesh_for_sup)
        self.obj_mesh_on_sup = deepcopy(obj_mesh_on_sup)
        self.obj_mesh_on_sup.apply_transform(obj_pose_on_sup)
        while cnt < n_trials:
            support_points = self.sample_supports(obj_mesh_on_sup, obj_pose_on_sup, n_samples_on_sup,
                                            obj_mesh_for_sup, obj_pose_for_sup, n_samples_for_sup)
            
            for result_obj_pose, obj_pose_transformed_for_sup, point_on_sup, point_transformed in self._transform_points_on_support(support_points, obj_pose_for_sup):
                T = np.dot(obj_pose_for_sup, np.linalg.inv(obj_pose_transformed_for_sup))
                self.obj_pose_transformed_for_sup = obj_pose_transformed_for_sup
                gripper_pose_transformed = np.dot(T, self.tcp_pose)
                result_gripper_pose = np.eye(4)
                result_gripper_pose[:3, :3] = gripper_pose_transformed[:3, :3]
                result_gripper_pose[:3, 3] = gripper_pose_transformed[:3, 3] + (point_on_sup - point_transformed) + np.array([0, 0, self.release_distance])

                gripper_transformed = self.get_gripper_transformed(result_gripper_pose)
                if self.collision_free(gripper_transformed, only_gripper=True):
                    release_pose = gripper_transformed[self.robot.eef_name]
                    yield release_pose, result_obj_pose
            cnt += 1

    def sample_supports(
        self,
        obj_mesh_on_sup,
        obj_pose_on_sup,
        n_samples_on_sup,
        obj_mesh_for_sup,
        obj_pose_for_sup,
        n_samples_for_sup,
    ):
        sample_points_on_support = self.generate_points_on_support(obj_mesh_on_sup, obj_pose_on_sup, n_samples_on_sup)
        sample_points_for_support = list(self.generate_points_for_support(obj_mesh_for_sup, obj_pose_for_sup, n_samples_for_sup))

        for point_on_support, normal_on_support in sample_points_on_support:
            for point_for_support, normal_for_support in sample_points_for_support:
                yield point_on_support, normal_on_support, point_for_support, normal_for_support

    def _transform_points_on_support(self, support_points, obj_pose_for_sup):
        for point_on_sup, normal_on_sup, point_for_sup, normal_for_sup in support_points:
            normal_on_sup = -normal_on_sup
            R_mat = get_rotation_from_vectors(normal_for_sup, normal_on_sup)
            
            obj_pose_transformed_for_sup = np.eye(4)
            obj_pose_transformed_for_sup[:3, :3] = np.dot(R_mat, obj_pose_for_sup[:3, :3])
            obj_pose_transformed_for_sup[:3, 3] = obj_pose_for_sup[:3, 3]

            point_transformed = np.dot(point_for_sup - obj_pose_for_sup[:3, 3], R_mat) + obj_pose_for_sup[:3, 3]
            # normal_transformed = np.dot(normal_for_sup, R_mat)

            result_obj_pose = np.eye(4)
            result_obj_pose[:3, :3] = obj_pose_transformed_for_sup[:3, :3]
            result_obj_pose[:3, 3] = obj_pose_for_sup[:3, 3] + (point_on_sup - point_transformed) + np.array([0, 0, self.release_distance])

            yield result_obj_pose, obj_pose_transformed_for_sup, point_on_sup, point_transformed

    def filter_supports(self, support_poses):
        is_success_filtered = False
        for release_pose, result_obj_pose in support_poses:
            if not self._check_support(result_obj_pose):
                continue

            qpos = self._compute_inverse_kinematics(release_pose)
            if qpos is None:
                continue

            transforms = self.robot.forward_kin(np.array(qpos))
            goal_pose = transforms[self.robot.eef_name].h_mat

            if self.obj_info:
                self.robot_c_manager.set_transform(self.obj_info["name"], result_obj_pose)
                
            if self._check_ik_solution(release_pose, goal_pose) and self.collision_free(transforms):
                pre_release_pose = self.get_pre_release_pose(release_pose)
                pre_qpos = self._compute_inverse_kinematics(pre_release_pose)
                pre_transforms = self.robot.forward_kin(np.array(pre_qpos))
                pre_goal_pose = pre_transforms[self.robot.eef_name].h_mat

                if self.obj_info:
                    obj_pre_release_pose = np.dot(pre_release_pose, self.T_between_gripper_and_obj)
                    self.robot_c_manager.set_transform(self.obj_info["name"], obj_pre_release_pose)
                    self.obj_pre_release_pose = obj_pre_release_pose

                if self._check_ik_solution(pre_release_pose, pre_goal_pose) and self.collision_free(pre_transforms):
                    self.pre_release_pose = pre_release_pose
                    
                    post_release_pose = self.get_post_release_pose(release_pose)
                    post_qpos = self._compute_inverse_kinematics(post_release_pose)
                    post_transforms = self.robot.forward_kin(np.array(post_qpos))
                    post_goal_pose = post_transforms[self.robot.eef_name].h_mat

                    if self.obj_info:
                        self.objects_c_manager.set_transform(self.obj_info["name"], result_obj_pose)
                        self.robot_c_manager.remove_object(self.obj_info["name"])

                    if self._check_ik_solution(post_release_pose, post_goal_pose) and self.collision_free(post_transforms):
                        self.post_release_pose = post_release_pose
                        self.result_obj_pose = result_obj_pose
                        is_success_filtered = True
                        break

        if not is_success_filtered:
            logger.error(f"Failed to filter release poses")
            return None
        
        if self.obj_info:
            self.result_object_c_manager.append(self.objects_c_manager)

        logger.info(f"Success to get release pose.\n")
        return release_pose

    def generate_points_on_support(
        self,
        obj_mesh,
        obj_pose,
        n_samples
    ):
        copied_mesh = deepcopy(obj_mesh)
        copied_mesh.apply_transform(obj_pose)

        weights = np.zeros(len(copied_mesh.faces))
        for idx, vertex in enumerate(copied_mesh.vertices[copied_mesh.faces]):
            weights[idx]=0.0
            if np.all(vertex[:,2] >= copied_mesh.bounds[1][2] * 0.98):                
                weights[idx] = 1.0

        support_points, face_ind, normal_vectors = surface_sampling(copied_mesh, n_samples, weights)
        for point, normal_vector in zip(support_points, normal_vectors):
            yield point, normal_vector

    def generate_points_for_support(
        self,
        obj_mesh,
        obj_pose,
        n_samples
    ):
        copied_mesh = deepcopy(obj_mesh)
        copied_mesh.apply_transform(obj_pose)
    
        weights = np.zeros(len(copied_mesh.faces))
        for idx, vertex in enumerate(copied_mesh.vertices[copied_mesh.faces]):
            weights[idx]=0.4
            if np.all(vertex[:,2] <= copied_mesh.bounds[0][2] * 1.02):                
                weights[idx] = 0.6
  
        support_points, face_ind, normal_vectors = surface_sampling(copied_mesh, n_samples, weights)
        for point, normal_vector in zip(support_points, normal_vectors):
            yield point, normal_vector

    def _compute_inverse_kinematics(self, grasp_pose):
        eef_pose = get_pose_from_homogeneous(grasp_pose)
        qpos = self.robot.inverse_kin(np.random.randn(7), eef_pose, maxIter=500)
        return qpos

    def _generate_contact_points(
        self,
        obj_mesh,
        obj_pose,
        limit_angle
    ):
        copied_mesh = deepcopy(obj_mesh)
        copied_mesh.apply_transform(obj_pose)

        while True:
            contact_points, _, normals = surface_sampling(copied_mesh, n_samples=2)
            if self._is_force_closure(contact_points, normals, limit_angle):
                break
        return (contact_points, normals)

    def _is_force_closure(self, vertices, normals, limit_angle):
        vectorA = vertices[0]
        vectorB = vertices[1]

        normalA = -normals[0]
        normalB = -normals[1]

        vectorAB = vectorB - vectorA
        distance = np.linalg.norm(vectorAB)

        unit_vectorAB = normalize(vectorAB)
        angle_A2AB = np.arccos(normalA.dot(unit_vectorAB))

        unit_vectorBA = -1 * unit_vectorAB
        angle_B2AB = np.arccos(normalB.dot(unit_vectorBA))

        if distance > self.gripper_max_width:
            return False

        if angle_A2AB > limit_angle or angle_B2AB > limit_angle:
            return False
        
        return True

    def _generate_grasp_directions(self, vector, n_trials):
        norm_vector = normalize(vector)
        e1, e2 = np.eye(3)[:2]
        v1 = e1 - projection(e1, norm_vector)
        v1 = normalize(v1)
        v2 = e2 - projection(e2, norm_vector) - projection(e2, v1)
        v2 = normalize(v2)

        for theta in np.linspace(-np.pi, np.pi, n_trials):
            normal_dir = np.cos(theta) * v1 + np.sin(theta) * v2
            yield normal_dir

    def _check_ik_solution(self, eef_pose, goal_pose, err_limit=1e-2) -> bool:
        error_pose = self.robot.get_pose_error(eef_pose, goal_pose)
        if error_pose < err_limit:
            return True
        return False

    def _check_support(self, obj_pose):
        obj_mesh = deepcopy(self.obj_mesh_for_sup)
        obj_mesh.apply_transform(obj_pose)
        self.obj_center_point = obj_mesh.center_mass
        locations, _, _ = self.obj_mesh_on_sup.ray.intersects_location(
                    ray_origins=[self.obj_center_point],
                    ray_directions=[[0, 0, -1]])

        if len(locations) != 0:
            support_index = np.where(locations == np.max(locations, axis=0)[2])
            self.obj_support_point = locations[support_index[0]]
            return True
        logger.warning("Not found support point")
        return False