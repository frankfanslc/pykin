import math
import numpy as np

from pykin.planners.planner import Planner
from pykin.planners.tree import Tree
from pykin.utils.fcl_utils import FclManager
from pykin.utils.kin_utils import get_robot_geom
from pykin.utils.error_utils import NotFoundError, CollisionError
from pykin.utils.transform_utils import get_homogeneous_matrix

class RRTStarPlanner(Planner):
    """
    RRT star spath planning
    """
    def __init__(
        self, 
        robot,
        obstacles=[],
        current_q=None,
        goal_q=None,
        delta_distance=0.5,
        epsilon=0.1,
        max_iter=3000,
        gamma_RRT_star=300, # At least gamma_RRT > delta_distance,
    ):
        self.robot = robot
        self.obstacles = obstacles
        self.cur_q = current_q
        self.goal_q  = goal_q
        self.delta_dis = delta_distance
        self.epsilon = epsilon
        self.max_iter = max_iter
        self.gamma_RRTs = gamma_RRT_star
        
        self.T = None
        self.cost = None

        self.arm = None
        self.dimension = self.robot.dof
        self.eef_name = self.robot.eef_name

    def setup_start_goal_joint(self, current_q, goal_q, arm=None, transformation=None):
        if transformation is None:
            transformation = self.robot.init_transformations

        if not isinstance(current_q, (np.ndarray)):
            current_q = np.array(current_q)
        
        if not isinstance(goal_q, (np.ndarray)):
            current_q = np.array(goal_q)
        
        if self._check_q_data(current_q, goal_q):
            self.cur_q = current_q
            self.goal_q  = goal_q

        self.arm = arm
        self.dimension = len(current_q)

        self._setup_collision_and_q_limit(transformation)

    @staticmethod
    def _check_q_data(current_q, goal_q):
        if current_q.all() or goal_q.all() is None:
            raise NotFoundError("Make sure set current or goal joints..")
        return True

    def _setup_collision_and_q_limit(self, transformation):
        self.fcl_manager = FclManager()
        self._setup_fcl_manager(transformation)
        self._set_q_limits()
        self._set_eef_name()

    def _setup_fcl_manager(self, transformatios):
        self._apply_fcl_to_robot(transformatios)
        self._apply_fcl_to_obstacles()
        self._check_init_collision()

    def _apply_fcl_to_robot(self, transformatios):
        for link, transformation in transformatios.items():
            name, gtype, gparam = get_robot_geom(self.robot.links[link])
            transform = transformation.homogeneous_matrix
            self.fcl_manager.add_object(name, gtype, gparam, transform)
    
    def _apply_fcl_to_obstacles(self):
        if self.obstacles:
            for name, (ob_x, ob_y, ob_z, ob_r) in self.obstacles.items():
                ob_transform = get_homogeneous_matrix(position=np.array([ob_x, ob_y, ob_z]))
                self.fcl_manager.add_object(name, "sphere", ob_r, ob_transform)

    def _check_init_collision(self):
        is_collision, obj_names = self.fcl_manager.collision_check(return_names=True)
        if is_collision:
            for name1, name2 in obj_names:
                if not ("obstacle" in name1 and "obstacle" in name2):
                    raise CollisionError((name1, name2))

    def generate_path(self):
        path = None
        self.T = Tree()
        self.cost = {}
        self.T.add_vertex(self.cur_q)
        self.cost[0] = 0

        for k in range(self.max_iter):
            rand_q = self.random_state()
            nearest_q, nearest_idx = self.nearest_neighbor(rand_q, self.T)
            new_q = self.new_state(nearest_q, rand_q)
   
            if k % 300 == 0 and k !=0:
                print(f"iter : {k}")

            if self.collision_free(new_q) and self._q_in_limits(new_q):
                neighbor_indexes = self.find_near_neighbor(new_q)               
                min_cost = self.get_new_cost(nearest_idx, nearest_q, new_q)
                min_cost, nearest_idx = self.get_minimum_cost(neighbor_indexes, new_q, min_cost, nearest_idx)
 
                self.T.add_vertex(new_q)
                new_idx = len(self.T.vertices) - 1
                self.cost[new_idx] = min_cost
                self.T.add_edge([nearest_idx, new_idx])

                self.rewire(neighbor_indexes, new_q, new_idx)

                if self.reach_to_goal(new_q):
                    path = self.find_path(self.T)

        return path

    def random_state(self):
        q_outs = np.zeros(self.dimension)
        if np.random.random() > self.epsilon:
            for i, (q_min, q_max) in enumerate(zip(self.q_limits_lower, self.q_limits_upper)):
                q_outs[i] = np.random.uniform(q_min, q_max)
        else:
            q_outs = self.goal_q
        return q_outs

    def _set_q_limits(self):
        if self.arm is not None:
            self.q_limits_lower = self.robot.joint_limits_lower[self.arm]
            self.q_limits_upper = self.robot.joint_limits_upper[self.arm]
        else:
            self.q_limits_lower = self.robot.joint_limits_lower
            self.q_limits_upper = self.robot.joint_limits_upper

    def _set_eef_name(self):
        if self.arm is not None:
            self.eef_name = self.robot.eef_name[self.arm]

    def _q_in_limits(self, q_in):
        return np.all([q_in >= self.q_limits_lower, q_in <= self.q_limits_upper])

    def nearest_neighbor(self, random_q, tree):
        distances = [self.distance(random_q, vertex) for vertex in tree.vertices]
        nearest_idx = np.argmin(distances)
        nearest_point = tree.vertices[nearest_idx]
        return nearest_point, nearest_idx

    def distance(self, pointA, pointB):
        return np.linalg.norm(pointB - pointA)

    def new_state(self, nearest_q, random_q):
        if np.equal(nearest_q, random_q).all():
            return nearest_q

        vector = random_q - nearest_q
        dist = self.distance(random_q, nearest_q)
        step = min(self.delta_dis, dist)
        unit_vector = vector / dist
        new_q = nearest_q + unit_vector * step

        return new_q

    def find_near_neighbor(self, q):
        card_V = len(self.T.vertices) + 1
        r = self.gamma_RRTs * ((math.log(card_V) / card_V) ** (1/self.dimension))
        search_radius = min(r, self.delta_dis)
        dist_list = [self.distance(vertex, q) for vertex in self.T.vertices]
                                                   
        near_indexes = []
        for idx, dist in enumerate(dist_list):
            if dist <= search_radius and self.collision_free(q):
                near_indexes.append(idx)

        return near_indexes

    def get_new_cost(self, idx, A, B):
        cost = self.cost[idx] + self.distance(A, B)
        return cost

    def get_minimum_cost(self, neighbor_indexes, new_q, min_cost, nearest_idx):
        for i in neighbor_indexes:
            new_cost = self.get_new_cost(i, new_q, self.T.vertices[i])

            if new_cost < min_cost and self.collision_free(new_q):
                min_cost = new_cost
                nearest_idx = i

        return min_cost, nearest_idx

    def rewire(self, neighbor_indexes, new_q, new_idx):
        for i in neighbor_indexes:
            no_collision = self.collision_free(self.T.vertices[i])
            new_cost = self.get_new_cost(new_idx, new_q, self.T.vertices[i])

            if no_collision and new_cost < self.cost[i]:
                self.cost[i] = new_cost
                self.T.edges[i-1][0] = new_idx

    def collision_free(self, new_q):
        transformations = self._get_transformations(new_q)
        for link, transformations in transformations.items():
            if link in self.fcl_manager._objs:
                transform = transformations.homogeneous_matrix
                self.fcl_manager.set_transform(name=link, transform=transform)

        is_collision , name = self.fcl_manager.collision_check(return_names=True, return_data=False)
        if not is_collision:
            return True
        return False

    def _get_transformations(self, q_in):
        if self.arm is not None:
            transformations = self.robot.forward_kin(q_in, self.robot.desired_frames[self.arm])
        else:
            transformations = self.robot.forward_kin(q_in, self.robot.desired_frames)
        return transformations

    def reach_to_goal(self, point):
        dist = self.distance(point, self.goal_q)
        if dist <= 0.2:
            return True
        return False

    def find_path(self, tree):
        path = [self.goal_q]
        goal_idx = tree.edges[-1][1]
 
        while goal_idx != 0:
            path.append(tree.vertices[goal_idx])
            parent_idx = tree.edges[goal_idx-1][0]
            goal_idx = parent_idx
        path.append(self.cur_q)

        return path[::-1]

    def get_rrt_tree(self):
        vertices = []
        for edge in self.T.edges:
            from_node = self.T.vertices[edge[0]]
            goal_node = self.T.vertices[edge[1]]
            vertices.append((from_node, goal_node))
        return vertices