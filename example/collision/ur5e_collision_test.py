import numpy as np
import trimesh
import yaml
import sys, os

pykin_path = os.path.dirname(os.path.dirname(os.getcwd()))
sys.path.append(pykin_path)

from pykin.robots.single_arm import SingleArm
from pykin.kinematics.transform import Transform
from pykin.collision.collision_manager import CollisionManager
from pykin.utils.collision_utils import apply_robot_to_collision_manager, apply_robot_to_scene


file_path = '../../asset/urdf/ur5e/ur5e.urdf'
robot = SingleArm(file_path, Transform(rot=[0.0, 0.0, 0.0], pos=[0, 0, -1]))

custom_fpath = '../../asset/config/ur5e_init_params.yaml'
with open(custom_fpath) as f:
            controller_config = yaml.safe_load(f)
init_qpos = controller_config["init_qpos"]
fk = robot.forward_kin(init_qpos)

mesh_path = pykin_path+"/asset/urdf/ur5e/"
c_manager = CollisionManager(mesh_path)
c_manager.setup_robot_collision(robot, fk, geom="collision")

result, objs_in_collision, contact_data = c_manager.in_collision_internal(return_names=True, return_data=True)
print(result, objs_in_collision, len(contact_data))

scene = trimesh.Scene()
scene = apply_robot_to_scene(scene=scene, mesh_path=mesh_path, robot=robot, fk=fk)
scene.set_camera(np.array([np.pi/2, 0, np.pi/2]), 5, resolution=(1024, 512))
scene.show()