"""Where a robot is, what is around it, and the places a person named.

A robot that knows its pose is a robot whose maker said where it is: the Microduck's contact
odometry, its onboard mapper `mapd`, MOSS's lidar, a ROS `/odom`, a simulator. quackd makes no map
of its own. What it adds is the meaning: a pose read the same way from every body (`pose.Fix`),
places a pilot or a person marked (`places`), an occupancy grid read from whoever made one
(`grid`), and the walk to a place on top of them.

A pose belongs to the frame it was read in, and so does every place marked in it. Two frames are
never converted into each other: odometry starts again whenever its robot is picked up, and a map
has no north. A place marked in a frame that is not the one the robot is in now is kept and
listed, and never walked to (ADR-0068).
"""
