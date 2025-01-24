"""
@Author: Yiting CHEN
@Email: chenyiting@whu.edu.cn
"""
import copy
import os
import open3d as o3d
import numpy as np
from JSDF import opt_JSDF, opt_arm_hand_JSDF
from scipy.optimize import minimize
from scipy.optimize import NonlinearConstraint
import time
from scipy.spatial.transform import Rotation as R
from motion_planning import robot_RRT_connect


class differentiate_solver:
    def __init__(self, method="SLSQP", used_link=None, obj_name=None, obj_position=None):
        np.random.seed(6)
        assert obj_name in ["lipton_tea", "orange", "power_drill", "potato_chip_1", "blue_moon", "MustardBottle",
                            "banana", "apple", "shampoo", "lemon", "plum", "suger_1", "soap", "glue", "strawberry"]

        if used_link is None:
            # 0~7 arm link 1-8, 8~9 palm, 10~14 index, 15~19 middle, 20~24 ring, 25~29 thumb
            # pinch grasp
            # used_link = [13, 14, 18, 19, 29]
            # used_link = [13, 14, 29]

            # power grasp
            # used_link = [9, 11, 12, 14, 16, 17, 19]
            # used_link = [9, 12, 17, 22]
            # used_link = [9, 17, 18]

            # used_link = [9, 17]
            # used_link = [9, 11, 12, 14, 16, 17, 19, 21, 22, 24]

            # arm wrapping
            used_link = [4, 13, 14, 18, 19]

            # arm-hand wrapping
            # used_link = []

        self.method = method
        self.jsdf = opt_arm_hand_JSDF()
        self.bounds = self.get_system_bounds(syn_type=1)
        sample = self.sample_random_syn_config()
        # sample = [0.]*14

        # power grasp

        sample = [0.61993265, 1.3694058, -1.4692691, -0.72924736, 0.23032162,
                  2.0943951, 0.2870809, 0.31679936, 0.25343902, 0.2141256,
                  0.263, 0., 0., 0.]

        # pinch grasp
        # sample[-7:] = [0.3, 0.3, 0.6, 1.5, 0.2, 0.2, 0.8]
        # sample = [2.93623403, -0.30387453, 2.96705973, -1.86910411, -1.14120443,
        #           0.4462168, 0.96085204, 0.89475324, 0.29999315, 1.13694191,
        #           1.3962616, 0.27285269, 0.10036106, 0.80614369]
        self.init_jp = sample
        # self.init_jp = self.jsdf.arm_hand_robot.sample_random_robot_config(syn_type=1)
        self.current_jp = self.init_jp
        self.links = used_link

        self.obj_name = obj_name
        self.obj_position = obj_position
        self.obj_pcd = None
        self.obj_grasp_points = None
        self.obj_full_points = None

        # load affordance area
        self.area_1 = None
        self.area_2 = None

        # add collisions

        self.sphere_collision = o3d.geometry.TriangleMesh.create_sphere(radius=0.11, resolution=30)
        self.sphere_collision.translate([0.51, 0.1, 0.35])
        self.sphere_collision.compute_vertex_normals()
        self.sphere_collision_pcd = self.sphere_collision.sample_points_poisson_disk(100)
        self.sphere_collision_points = np.asarray(self.sphere_collision_pcd.points)
        self.sphere_collision.paint_uniform_color(np.array([255, 255, 0]) / 255)

        self.sphere_1_collision = o3d.geometry.TriangleMesh.create_sphere(radius=0.14, resolution=30)
        self.sphere_1_collision.translate([0.51, 0.1, 0.14])
        self.sphere_1_collision.compute_vertex_normals()
        self.sphere_1_collision_pcd = self.sphere_1_collision.sample_points_poisson_disk(100)
        self.sphere_1_collision_points = np.asarray(self.sphere_1_collision_pcd.points)
        self.sphere_1_collision.paint_uniform_color(np.array([255, 255, 0]) / 255)

        self.table_collision = o3d.geometry.TriangleMesh.create_box(width=1.,
                                                                    height=1.,
                                                                    depth=0.01)
        self.table_collision.translate([0., -0.5, -0.01])
        self.table_collision.compute_vertex_normals()
        self.table_collision_pcd = self.table_collision.sample_points_poisson_disk(100)
        self.table_collision_points = np.asarray(self.table_collision_pcd.points)
        collision_color = np.array([200, 200, 200]) / 255
        self.table_collision.paint_uniform_color(collision_color)

        # self.obj_full_points = self.sphere_collision_points
        self.obj_mesh = None
        self.load_object()

        # visualization
        o3d.visualization.gui.Application.instance.initialize()
        self.vis = o3d.visualization.O3DVisualizer("QAQ")
        # self.ViewCtr = self.vis.get_view_control()
        self.vis.ground_plane = o3d.visualization.rendering.Scene.GroundPlane.XY
        self.vis.show_skybox(False)
        self.vis.scene_shader = o3d.visualization.O3DVisualizer.Shader.STANDARD
        self.vis.point_size = 1
        # self.vis.enable_raw_mode(True)
        bg_color = np.array([[0.8, 0.8, 0.8, 1]], dtype=np.float32).T
        self.vis.set_background(bg_color, None)
        self.vis.size = o3d.visualization.gui.Size(1920, 1280)
        self.vis.scene.scene.enable_sun_light(False)
        # self.vis.size = o3d.visualization.gui.Size(1920, 1280)

        self.vis.add_geometry({'name': 'robot', 'geometry': self.get_robot_mesh()})
        self.vis.add_geometry({'name': 'sphere', 'geometry': self.sphere_collision})
        self.vis.add_geometry({'name': 'sphere_1', 'geometry': self.sphere_1_collision})
        self.vis.add_geometry({'name': 'table', 'geometry': self.table_collision})

        parameters = o3d.io.read_pinhole_camera_parameters("ScreenCamera.json")
        self.intrinsic_m = parameters.intrinsic
        self.extrinsic_m = parameters.extrinsic
        self.vis.setup_camera(parameters.intrinsic, parameters.extrinsic)
        self.vis.scene.scene.enable_sun_light(False)
        self.app = o3d.visualization.gui.Application.instance
        self.app.add_window(self.vis)

        # self.differential_optimization_vis()

        # goal_state = self.differential_optimization()
        np.random.seed(4)
        env_points = np.concatenate((self.obj_full_points, self.sphere_1_collision_points))
        # #
        self.planner = robot_RRT_connect(min_dist=-0.03,
                                         env_points=env_points[::3],
                                         use_GPU=True)
        self.motion_planning_vis()

    def get_system_bounds(self, syn_type=1):
        upper_bounds = self.jsdf.arm_hand_robot.arm.joint_upper_bound
        lower_bounds = self.jsdf.arm_hand_robot.arm.joint_lower_bound
        bounds = [(lower_bounds[i], upper_bounds[i]) for i in range(7)]
        if syn_type == 1:
            # pinch grasp
            # hand_bounds = [(0, 1), (0, 0.3), (1, 1.4),
            #                (1.3, 1.6), (0, 0.5), (0.1, 0.3), (0.8, 1)]

            # power grasp
            # hand_bounds = [(-0.196, 1.618), (-0.174, 1.618), (-0.227, 1.),
            #                (0.263, 1.396), (-0.105, 2), (-0.189, 1.644), (-0.162, 1.719)]

            # arm wrapping
            hand_bounds = [(-0.196, 1), (-0.174, 1), (-0.227, 1),
                           (0.263, 1.396), (-0.105, 2), (-0.189, 1.644), (-0.162, 1.719)]

        elif syn_type == 2:
            hand_bounds = [(-0.196, 1.61), (-0.174, 1.709), (-0.227, 1.618)]

        else:
            raise ValueError

        bounds = bounds + hand_bounds
        return bounds

    def sample_random_syn_config(self):
        config = []
        for bound in self.bounds:
            config.append(np.random.uniform(low=bound[0], high=bound[1]))

        return config

    def get_rotation_matrix(self):
        rot = R.from_euler('xyz', angles=[0, 0, 1], degrees=True)
        T = np.eye(4)
        # print(T)
        T[:3, :3] = rot.as_matrix()
        return T

    def get_material(self):
        material = o3d.visualization.rendering.MaterialRecord()
        material.shader = "defaultLitTransparency"  # This shader will consider lighting
        material.base_reflectance = 0.2

        return material

    def motion_planning_vis(self):
        self.vis.remove_geometry("robot")
        goal_state = [0.5717223, 1.6, -1.57, -0.7995966, 0.0,
                      2.0943951, 0.1616571, 1., 0.65394598, 0.32134594,
                      0.263, 0., 0., 0.]
        self.current_jp = goal_state
        # self.add_robot_geometry()

        robot_mesh = self.get_robot_mesh()
        self.vis.add_geometry({'name': 'robot_gaol',
                               'geometry': robot_mesh})
        # self.app.run()
        start_state = [-0.0721567820882899, 0.6870185433177471, -1.4362976474767537, -0.8887506858103579,
                       -0.2693628733675883, 1.1264620153585245, -0.3715346026419649]

        state_sequence = self.planner.plan(q_start=np.array(start_state),
                                           q_target=np.array(goal_state[:7]))
        print("state length", len(state_sequence))

        state_sequence = state_sequence[::20]
        print(state_sequence)
        # state_sequence = [
        #     [-0.8205795806291198, -0.15793950102209225, -1.5993600650167905, -1.0575374492106109, -0.9331634487525602,
        #      0.09293634963242348, -1.0144158325975352, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [-0.6567793593786351, 0.03279719086286009, -1.5744098208971677, -1.0271914669505393, -0.7894359794875532,
        #      0.32840208496978585, -0.8760543111154724, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [-0.4929791381281505, 0.22353388274781244, -1.5494595767775454, -0.9968454846904675, -0.6457085102225462,
        #      0.5638678203071482, -0.7376927896334092, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [-0.3291789168776658, 0.4142705746327648, -1.5245093326579227, -0.9664995024303954, -0.5019810409575391,
        #      0.7993335556445106, -0.599331268151346, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [-0.16537869562718116, 0.6050072665177169, -1.4995590885383003, -0.9361535201703233, -0.3582535716925321,
        #      1.0347992909818728, -0.46096974666928286, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [-0.0015784743766965072, 0.7957439584026686, -1.4746088444186778, -0.9058075379102513, -0.21452610242752507,
        #      1.270265026319235, -0.32260822518722015, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [0.16222174687378815, 0.9864806502876204, -1.4496586002990552, -0.8754615556501791, -0.07079863316251801,
        #      1.5057307616565967, -0.18424670370515733, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [0.3260219681242728, 1.1772173421725722, -1.4247083561794327, -0.8451155733901073, 0.07292883610248915,
        #      1.741196496993958, -0.045885182223094394, 0, 0, 0, 0.263, 0.0, 0.0, 0.0],
        #     [0.48982218937475747, 1.367954034057524, -1.3997581120598108, -0.8147695911300358, 0.2166563053674964,
        #      1.9766622323313192, 0.0924763392589685, 0, 0, 0, 0.263, 0.0, 0.0, 0.0]]
        for i, state in enumerate(state_sequence):
            temp_state = copy.deepcopy(self.init_jp)
            temp_state[:7] = state
            temp_state[7:10] = [0, 0, 0]
            self.current_jp = temp_state
            # self.add_robot_geometry()
            robot_mesh = self.get_robot_mesh()
            self.vis.add_geometry({'name': 'robot_{}'.format(i),
                                   'geometry': robot_mesh})
            time.sleep(0.02)
            self.app.run_one_tick()

        self.app.run()

    def differential_optimization(self):
        bounds = self.get_system_bounds()
        res = minimize(
            self.objective_func,
            method="SLSQP",
            x0=np.array(self.init_jp),
            jac=self.gradient_func,
            bounds=bounds,
            tol=0.000001
            # constraints={'type': 'ineq', 'fun': self.arm_collision_free_constraint}
        )
        print(res)
        return res.x

    def differential_optimization_vis(self):
        bounds = self.get_system_bounds()

        res = minimize(
            self.objective_func,
            method="SLSQP",
            x0=np.array(self.init_jp),
            jac=self.gradient_func,
            bounds=bounds,
            tol=0.00001,
            # constraints={'type': 'ineq', 'fun': self.obj_collision_free_constraint}
        )
        print("Optimization Finished")
        print(res)

        def update_camera():
            self.extrinsic_m = self.extrinsic_m @ self.get_rotation_matrix()
            self.vis.setup_camera(self.intrinsic_m, self.extrinsic_m)

        def thread_main():
            while True:
                # Rotation
                # Update geometry
                o3d.visualization.gui.Application.instance.post_to_main_thread(self.vis, update_camera)
                time.sleep(0.05)

        import threading
        # threading.Thread(target=thread_main).start()

        o3d.visualization.gui.Application.instance.run()

    def add_robot_geometry(self):
        # q = np.asarray(q)
        robot_mesh = self.get_robot_mesh()
        self.vis.remove_geometry('robot')
        self.vis.add_geometry({'name': 'robot',
                               'geometry': robot_mesh})

    def load_object(self, every_k=100):
        if self.obj_position is None:
            self.obj_position = [0.7, 0., 0.34]

        self.obj_pcd = o3d.io.read_point_cloud("objects/" + self.obj_name + "/visual.ply")
        self.obj_pcd = self.obj_pcd.uniform_down_sample(every_k)
        self.obj_pcd.translate(self.obj_position)

        self.obj_mesh = o3d.io.read_triangle_mesh("objects/" + self.obj_name + "/textured.obj", True)
        self.obj_mesh.translate(self.obj_position)
        # self.obj_mesh.rotate(R.from_euler('xyz', degrees=True, angles=[0, 10, 0]).as_matrix())
        self.area_1 = o3d.io.read_point_cloud("objects/" + self.obj_name + "/area_1.ply")
        self.area_1.translate(self.obj_position)
        self.area_1 = np.asarray(self.area_1.points)

        self.area_2 = o3d.io.read_point_cloud("objects/" + self.obj_name + "/area_2.ply")
        self.area_2.translate(self.obj_position)
        self.area_2 = np.asarray(self.area_2.points)

        self.obj_grasp_points = np.concatenate((self.area_1, self.area_2))
        self.obj_pcd = self.obj_mesh.sample_points_poisson_disk(3000)

        self.obj_full_points = np.asarray(self.obj_pcd.points)
        self.obj_full_points = self.sphere_collision_points

    @staticmethod
    def trimesh_mesh2o3d(mesh):
        o3d_mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(mesh.vertices),
                                             o3d.utility.Vector3iVector(mesh.faces))

        o3d_mesh.compute_vertex_normals()
        vertex_colors = np.asarray(mesh.visual.to_color().vertex_colors)
        o3d_mesh.vertex_colors = o3d.utility.Vector3dVector(vertex_colors[:, :3] / 255)
        # o3d_mesh.compute_vertex_normals()

        return o3d_mesh

    def get_robot_mesh(self):
        print(self.current_jp)
        self.jsdf.arm_hand_robot.set_system_config(self.current_jp, syn_type=1)
        robot_mesh = self.jsdf.arm_hand_robot.get_system_combined_mesh()
        # robot_mesh.show()
        o3d_robot_mesh = self.trimesh_mesh2o3d(robot_mesh)

        return o3d_robot_mesh

    def objective_func(self, robot_q):
        self.current_jp = robot_q
        self.add_robot_geometry()
        time.sleep(0.1)
        self.app.run_one_tick()
        # self.vis.poll_events()
        # self.vis.update_renderer()
        return self.jsdf.calculate_squared_dist_hand_link(robot_q, link_index=self.links, position=self.obj_full_points)

    def gradient_func(self, robot_q):
        gradient = self.jsdf.calculate_gradient_dist_hand_link(robot_q, link_index=self.links,
                                                               position=self.obj_full_points)
        return gradient.cpu().detach().numpy()

    def obj_collision_free_constraint(self, robot_q):
        all_link_index = [i for i in range(30)]
        for links in self.links:
            all_link_index.remove(links)
        points = np.concatenate((self.sphere_1_collision_points, self.sphere_1_collision_points))

        dist_min = self.jsdf.whole_arm_constraint_inference(position=np.concatenate((self.obj_full_points,
                                                                                     points)),
                                                            q=robot_q,
                                                            return_grad=False,
                                                            link_index=all_link_index)
        dist_min = dist_min.detach().numpy() + 0.005
        return dist_min

    def obj_collision_free_constraint_grad(self, robot_q):
        points = np.concatenate((self.sphere_1_collision_points, self.sphere_1_collision_points))

        all_link_index = [i for i in range(30)]
        for links in self.links:
            all_link_index.remove(links)

        _, grad = self.jsdf.whole_arm_constraint_inference(position=np.concatenate((self.obj_full_points,
                                                                                    points)),
                                                           q=robot_q,
                                                           return_grad=True,
                                                           link_index=all_link_index)
        return grad

    def arm_collision_free_constraint(self, robot_q):
        arm_link_index = [i for i in range(29)]

        points = np.concatenate((self.sphere_collision_points, self.box_collision_points, self.sphere_collision_points))
        dist_min = self.jsdf.whole_arm_constraint_inference(position=points,
                                                            q=robot_q,
                                                            return_grad=False,
                                                            link_index=arm_link_index)
        dist_min = dist_min.detach().numpy() - 0.01
        return dist_min


if __name__ == "__main__":
    # TODO: Add box sphere cylinder for collision avoidance.
    solver = differentiate_solver(obj_name='MustardBottle')
    # solver.current_jp = [0.5] * 10
    # solver.get_robot_mesh()

    # o3d.visualization.draw_geometries([solver.obj_mesh, solver.get_robot_mesh()])
