"""The only file in quackd allowed to spell a roslibpy or rosbridge name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(an assumption of ours, with what quackd does about it). `docs/adapters/rosbridge/README.md` is
the human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `ws` backend.

Three upstreams, each pinned and read on 2026-09-02: roslibpy (the client library, PyPI
2.1.0, Python >= 3.9), rosbridge_suite (the protocol the server speaks, ros2 branch) and
ros2/common_interfaces (the message definitions). Two more were pinned and read on
2026-09-13 for introspection, which asks the bridge what the body under it is: ros/urdfdom
(what a URDF says) and ros/robot_state_publisher (where the URDF usually is). The action refs
at the end were read on 2026-11-08 at the same two pins, for the industrial arm family over this
binding (`quackd_rosarm`), which imports them from here by name. Nothing here has been run
against a bridge, and roslibpy is never imported outside the `ws` backend.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/gramaziokohler/roslibpy"
PIN = "f5793dbc28578275b0ac93ae84b736dad822db4b"
REPO_ROSBRIDGE = "https://github.com/RobotWebTools/rosbridge_suite"
PIN_ROSBRIDGE = "aa9a7a33ddb3b1b45ddd6d2eead4c3d5eb800b14"
REPO_INTERFACES = "https://github.com/ros2/common_interfaces"
PIN_INTERFACES = "d54aa9b96dfed4922775652414e0e8fa4ee2ae40"
REPO_URDFDOM = "https://github.com/ros/urdfdom"
PIN_URDFDOM = "bfcf29f39cc3e0fa13e5572f36b4b8c42d4f5ce1"
REPO_RSP = "https://github.com/ros/robot_state_publisher"
PIN_RSP = "5526c766f79537f233903d3d7cabd33cb97c44e6"
READ_ON = "2026-09-02"
READ_ON_INTROSPECTION = "2026-09-13"
READ_ON_ACTIONS = "2026-11-08"
PYPI_VERSION_READ = "2.1.0"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def bridge(path: str, line: int | None = None) -> str:
    return f"{REPO_ROSBRIDGE}/blob/{PIN_ROSBRIDGE}/{path}" + (f"#L{line}" if line else "")


def msg(path: str, line: int | None = None) -> str:
    return f"{REPO_INTERFACES}/blob/{PIN_INTERFACES}/{path}" + (f"#L{line}" if line else "")


def urdfdom(path: str, line: int | None = None) -> str:
    return f"{REPO_URDFDOM}/blob/{PIN_URDFDOM}/{path}" + (f"#L{line}" if line else "")


def rsp(path: str, line: int | None = None) -> str:
    return f"{REPO_RSP}/blob/{PIN_RSP}/{path}" + (f"#L{line}" if line else "")


_ROS = "src/roslibpy/ros.py"
_CORE = "src/roslibpy/core.py"
_COMM = "src/roslibpy/comm/comm.py"
_AUTOBAHN = "src/roslibpy/comm/comm_autobahn.py"
_PROTO = "ROSBRIDGE_PROTOCOL.md"
_LOADER = "rosbridge_library/src/rosbridge_library/internal/ros_loader.py"
_CONV = "rosbridge_library/src/rosbridge_library/internal/message_conversion.py"
_SUBSCRIBERS = "rosbridge_library/src/rosbridge_library/internal/subscribers.py"
_ROSAPI_NODE = "rosapi/scripts/rosapi_node"
_ROSAPI_PARAMS = "rosapi/src/rosapi/params.py"
_LINK_CPP = "urdf_parser/src/link.cpp"
_JOINT_CPP = "urdf_parser/src/joint.cpp"
_RSP_CPP = "src/robot_state_publisher.cpp"
_SEND_GOAL = "rosbridge_library/src/rosbridge_library/capabilities/send_action_goal.py"
_ACTIONS = "rosbridge_library/src/rosbridge_library/internal/actions.py"
_CAPABILITY = "rosbridge_library/src/rosbridge_library/capability.py"
_HANDLER = "rosbridge_server/src/rosbridge_server/websocket_handler.py"
_LAUNCH = "rosbridge_server/launch/rosbridge_websocket_launch.xml"
_NODE = "rosbridge_server/scripts/rosbridge_websocket.py"
_CHANGELOG = "rosbridge_library/CHANGELOG.rst"
_ROSAPI_PROXY = "rosapi/src/rosapi/proxy.py"

# ── roslibpy, the client ────────────────────────────────────────────────────────────────

PACKAGE = UpstreamRef(
    "roslibpy", "VERIFIED", src("src/roslibpy/__version__.py", 7), "2.1.0 at the pin and on PyPI"
)
ROS_CLIENT = UpstreamRef(
    "roslibpy.Ros(host, port=None, is_secure=False, headers=None, transport=None)",
    "VERIFIED",
    src(_ROS, 38),
    "the constructor already calls connect() (line 58); run() then waits for it",
)
ROS_RUN = UpstreamRef(
    "Ros.run(timeout)",
    "VERIFIED",
    src(_ROS, 109),
    "starts the non-blocking event loop and waits until connected or raises RosTimeoutError",
)
ROS_CLOSE = UpstreamRef("Ros.close(timeout)", "VERIFIED", src(_ROS, 93))
ROS_TERMINATE = UpstreamRef(
    "Ros.terminate()", "VERIFIED", src(_ROS, 154), "closes if connected, then stops the loop"
)
ROS_IS_CONNECTED = UpstreamRef("Ros.is_connected", "VERIFIED", src(_ROS, 71))
ROS_ON_READY = UpstreamRef("Ros.on_ready(callback, run_in_thread=True)", "VERIFIED", src(_ROS, 187))
ROS_GET_TOPICS = UpstreamRef("Ros.get_topics(callback, errback)", "VERIFIED", src(_ROS, 333))
ROS_GET_TOPIC_TYPE = UpstreamRef(
    "Ros.get_topic_type(topic, callback, errback)", "VERIFIED", src(_ROS, 352)
)
TOPIC = UpstreamRef(
    "roslibpy.Topic(ros, name, message_type, compression=None, latch=False, throttle_rate=0, "
    "queue_size=100, queue_length=0, reconnect_on_close=True)",
    "VERIFIED",
    src(_CORE, 210),
)
TOPIC_PUBLISH = UpstreamRef(
    "Topic.publish(message)",
    "VERIFIED",
    src(_CORE, 303),
    "advertises on first use (line 309) and sends dict(message), so a plain dict works",
)
TOPIC_SUBSCRIBE = UpstreamRef(
    "Topic.subscribe(callback)", "VERIFIED", src(_CORE, 260), "callback(message: dict)"
)
TOPIC_UNSUBSCRIBE = UpstreamRef("Topic.unsubscribe()", "VERIFIED", src(_CORE, 290))
TOPIC_ADVERTISE = UpstreamRef("Topic.advertise()", "VERIFIED", src(_CORE, 324))
TOPIC_UNADVERTISE = UpstreamRef("Topic.unadvertise()", "VERIFIED", src(_CORE, 363))
TOPIC_COMPRESSION = UpstreamRef(
    "compression: png or none", "VERIFIED", src(_CORE, 208), "quackd subscribes with none"
)
MESSAGE = UpstreamRef("roslibpy.Message(values)", "VERIFIED", src(_CORE, 45), "a UserDict")
WIRE_JSON = UpstreamRef(
    "JSON text frames", "VERIFIED", src(_COMM, 44), "json.loads on every frame, keyed by op"
)

# ── the rosbridge protocol, the server side ─────────────────────────────────────────────

OP_ADVERTISE = UpstreamRef(
    "op=advertise {id, topic, type, latch, queue_size}",
    "VERIFIED",
    bridge(_PROTO, 299),
    "what roslibpy sends (core.py line 334)",
)
OP_PUBLISH = UpstreamRef(
    "op=publish {id, topic, msg, latch}", "VERIFIED", bridge(_PROTO, 339), "core.py line 315"
)
OP_SUBSCRIBE = UpstreamRef(
    "op=subscribe {id, topic, type, compression, throttle_rate, queue_length}",
    "VERIFIED",
    bridge(_PROTO, 379),
    "core.py line 279",
)
BINARY_BASE64 = UpstreamRef(
    "uint8[] fields arrive base64-encoded",
    "VERIFIED",
    bridge(_PROTO, 140),
    "message_conversion.py line 96 lists the binary types; a CompressedImage's data is one",
)
TYPE_STRING_FORMS = UpstreamRef(
    "pkg/Type and pkg/msg/Type both resolve",
    "VERIFIED",
    bridge(_LOADER, 254),
    "_splittype accepts two or three parts; quackd sends the ROS 2 three-part form",
)

# ── the messages quackd speaks ──────────────────────────────────────────────────────────

MSG_TWIST = UpstreamRef(
    "geometry_msgs/msg/Twist",
    "VERIFIED",
    msg("geometry_msgs/msg/Twist.msg", 3),
    "{linear: Vector3, angular: Vector3}",
)
MSG_VECTOR3 = UpstreamRef(
    "geometry_msgs/msg/Vector3", "VERIFIED", msg("geometry_msgs/msg/Vector3.msg", 7), "{x, y, z}"
)
MSG_COMPRESSED_IMAGE = UpstreamRef(
    "sensor_msgs/msg/CompressedImage",
    "VERIFIED",
    msg("sensor_msgs/msg/CompressedImage.msg", 41),
    "{header, format, data: uint8[]}; format names the codec (jpeg, png) and pixel format",
)
MSG_ODOMETRY = UpstreamRef(
    "nav_msgs/msg/Odometry",
    "VERIFIED",
    msg("nav_msgs/msg/Odometry.msg", 12),
    "{header, child_frame_id, pose: PoseWithCovariance, twist: TwistWithCovariance}",
)
MSG_POSE = UpstreamRef(
    "geometry_msgs/msg/Pose",
    "VERIFIED",
    msg("geometry_msgs/msg/Pose.msg", 3),
    "{position: Point, orientation: Quaternion}; PoseWithCovariance wraps it as pose",
)
MSG_QUATERNION = UpstreamRef(
    "geometry_msgs/msg/Quaternion",
    "VERIFIED",
    msg("geometry_msgs/msg/Quaternion.msg", 3),
    "{x, y, z, w}",
)

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

NO_DEADMAN = UpstreamRef(
    "NO_DEADMAN",
    "UNVERIFIED",
    bridge(_PROTO),
    "neither rosbridge nor a base's driver has a deadman we verified; quackd re-sends the "
    "Twist at 10 Hz while a verb runs and publishes a zero Twist on stop, and that is the "
    "only stop authority (manifest: native none, deadman false)",
)
TOPIC_NAMES = UpstreamRef(
    "TOPIC_NAMES",
    "UNVERIFIED",
    bridge(_PROTO, 339),
    "/cmd_vel, /odom and an image topic are conventions, not a contract; each is set in "
    "the address query (?cmd_vel=&odom=&image=)",
)
TWIST_UNITS = UpstreamRef(
    "TWIST_UNITS",
    "UNVERIFIED",
    msg("geometry_msgs/msg/Twist.msg", 1),
    "m/s and rad/s in the base frame with angular z positive to the left, as ROS convention "
    "says; the limits in the manifest are what quackd clamps to, not what the base can do",
)
IMAGE_FORMAT = UpstreamRef(
    "IMAGE_FORMAT",
    "UNVERIFIED",
    msg("sensor_msgs/msg/CompressedImage.msg", 10),
    "the format string names jpeg or png and bgr8 or rgb8; quackd decodes with PIL and "
    "swaps channels when the format says bgr8, otherwise assumes rgb; anything else is refused",
)
ODOM_YAW = UpstreamRef(
    "ODOM_YAW",
    "UNVERIFIED",
    msg("nav_msgs/msg/Odometry.msg", 12),
    "yaw is taken from the quaternion's z and w, assuming a planar base",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_CORE, 260),
    "subscription callbacks arrive on roslibpy's transport thread; quackd stores only the "
    "latest message under a lock and never calls back into the event loop from there",
)
ROS1_BRIDGE = UpstreamRef(
    "ROS1_BRIDGE",
    "UNVERIFIED",
    bridge(_LOADER, 254),
    "a ROS 1 rosbridge should accept the three-part type strings too; not tried",
)


# ── introspection: what the bridge can be asked about the body (read 2026-09-13) ────────

SERVICE = UpstreamRef(
    "roslibpy.Service(ros, name, service_type, reconnect_on_close=True)",
    "VERIFIED",
    src(_CORE, 403),
    "the constructor of the class at line 391",
)
SERVICE_CALL = UpstreamRef(
    "Service.call(request, callback=None, errback=None, timeout=None)",
    "VERIFIED",
    src(_CORE, 423),
    "with no callback it blocks on call_sync_service(message, timeout) (line 461) and raises "
    "ServiceException (line 385) when the answer carries one; quackd calls it in a worker "
    "thread with the remaining deadline",
)
SERVICE_REQUEST = UpstreamRef(
    "roslibpy.ServiceRequest(values)", "VERIFIED", src(_CORE, 122), "a UserDict, like Message"
)
OP_CALL_SERVICE = UpstreamRef(
    "op=call_service {id, service, args}",
    "VERIFIED",
    bridge(_PROTO, 458),
    "protocol 4.4.3; the answer is op=service_response {id, service, values, result} (4.4.4). "
    "No type on the wire: the server resolves it",
)
ROSAPI_NODE = UpstreamRef(
    "rosapi",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 103),
    "the node registers its services as ~/topics and ~/get_param, so on the stock node name "
    "they are /rosapi/topics and /rosapi/get_param; a bridge launched without rosapi answers "
    "neither, and quackd records that it discovered nothing",
)
SVC_TOPICS = UpstreamRef(
    "/rosapi/topics",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 103),
    "create_service(Topics, '~/topics'); the handler fills response.topics and response.types "
    "(line 204)",
)
SRV_TOPICS = UpstreamRef(
    "rosapi_msgs/srv/Topics",
    "VERIFIED",
    bridge("rosapi_msgs/srv/Topics.srv"),
    "no request fields; {string[] topics, string[] types}",
)
SVC_GET_PARAM = UpstreamRef(
    "/rosapi/get_param",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 166),
    "create_service(GetParam, '~/get_param')",
)
SRV_GET_PARAM = UpstreamRef(
    "rosapi_msgs/srv/GetParam",
    "VERIFIED",
    bridge("rosapi_msgs/srv/GetParam.srv"),
    "{string name, string default_value} -> {string value, bool successful, string reason}",
)
PARAM_NAME_FORM = UpstreamRef(
    "node:parameter",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 568),
    "the ROS 2 rosapi splits request.name on a colon into a node and a parameter "
    "(`return tuple(param.split(':'))`), and params.py makes the node name absolute (line 311)",
)
PARAM_VALUE_JSON = UpstreamRef(
    "get_param answers with JSON",
    "VERIFIED",
    bridge(_ROSAPI_PARAMS, 319),
    "`return dumps(value)`, so a string parameter arrives quoted; quackd json-loads it and "
    "keeps the raw text when that fails",
)
ROS_GET_PARAM = UpstreamRef(
    "Ros.get_param(name, callback=None, errback=None)",
    "VERIFIED",
    src(_ROS, 520),
    "what roslibpy offers; quackd calls the service itself because this one json-loads inside "
    "roslibpy's own callback thread, where a raw-text answer would raise where nobody catches it",
)
SUBSCRIBE_QOS = UpstreamRef(
    "subscribe carries an optional qos",
    "VERIFIED",
    bridge(_PROTO, 396),
    "protocol 4.3.4; roslibpy 2.1.0's Topic.subscribe never sends one, so quackd cannot ask "
    "for transient_local and relies on the next entry instead",
)
SUBSCRIBER_QOS_FOLLOWS_PUBLISHERS = UpstreamRef(
    "a subscription is transient_local when every publisher on the topic is",
    "VERIFIED",
    bridge(_SUBSCRIBERS, 178),
    "the default is depth 10, VOLATILE, BEST_EFFORT (lines 170 to 174); durability follows the "
    "publishers when all of them are transient-local (lines 178 to 182), which is how a late "
    "subscriber sees a robot_description published at startup",
)
MSG_STRING = UpstreamRef(
    "std_msgs/msg/String", "VERIFIED", msg("std_msgs/msg/String.msg", 1), "{data}"
)
RSP_NODE = UpstreamRef(
    "robot_state_publisher",
    "VERIFIED",
    rsp(_RSP_CPP, 101),
    "the node name; it declares a robot_description parameter (line 120) unless "
    "use_robot_description_topic is set (line 103), and then it subscribes to the topic and "
    "declares no such parameter (lines 105 to 107)",
)
DESCRIPTION_TOPIC = UpstreamRef(
    "/robot_description",
    "VERIFIED",
    rsp(_RSP_CPP, 125),
    "create_publisher<std_msgs::msg::String>('robot_description', QoS(1).transient_local())",
)
DESCRIPTION_PARAM = UpstreamRef(
    "/robot_state_publisher:robot_description",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 568),
    "RSP_NODE's parameter written in PARAM_NAME_FORM; the default of the address's ?urdf_param=",
)
URDF_MASS = UpstreamRef(
    "link/inertial/mass@value",
    "VERIFIED",
    urdfdom(_LINK_CPP, 287),
    "parseInertial requires a mass element with a value attribute; the inertial element "
    "itself is optional per link (line 429)",
)
URDF_JOINT_TYPE = UpstreamRef(
    "joint@type in planar, floating, revolute, continuous, prismatic, fixed",
    "VERIFIED",
    urdfdom(_JOINT_CPP, 406),
    "the six strings parseJoint accepts, in that order (lines 406 to 416)",
)
URDF_JOINT_LIMIT = UpstreamRef(
    "joint/limit@lower,upper,effort,velocity",
    "VERIFIED",
    urdfdom(_JOINT_CPP, 102),
    "lower (102), upper (118), effort (134) and velocity (150); lower and upper are optional",
)
URDF_JOINT_TREE = UpstreamRef(
    "joint@name, joint/parent@link, joint/child@link",
    "VERIFIED",
    urdfdom(_JOINT_CPP, 342),
    "name (342), parent link (371), child link (386)",
)

# ── UNVERIFIED: what quackd assumes about a description it has never actually read ──────

DESCRIPTION_NAMES = UpstreamRef(
    "DESCRIPTION_NAMES",
    "UNVERIFIED",
    rsp(_RSP_CPP, 101),
    "that the description lives on a node called /robot_state_publisher, un-namespaced, and "
    "as a parameter rather than only on the topic, which it is not on a node started with "
    "use_robot_description_topic; both names come from the address (?urdf_param=, "
    "?urdf_topic=) and either can be turned off",
)
URDF_MASS_IS_THE_BODY = UpstreamRef(
    "URDF_MASS_IS_THE_BODY",
    "UNVERIFIED",
    urdfdom(_LINK_CPP, 287),
    "that the sum of the links' inertial masses is what the robot weighs. It is the robot's "
    "own file, so quackd tags the figure official, but a URDF may leave inertials out (the "
    "note says how many links had one) or model a tool or a payload as a link",
)
DOF_IS_NON_FIXED_JOINTS = UpstreamRef(
    "DOF_IS_NON_FIXED_JOINTS",
    "UNVERIFIED",
    urdfdom(_JOINT_CPP, 416),
    "that every joint whose type is not fixed is a degree of freedom, so wheels, casters, "
    "gripper fingers and mimic joints all count; no joints at all reports 0 and an unreadable "
    "description reports nothing",
)


# ── actions: what a client of an action server sends and hears (read 2026-11-08) ──────────
# Nothing in this adapter sends a goal. The industrial arm family (`quackd_rosarm`) does, over
# this binding, and imports these by name, so a re-pin of rosbridge or roslibpy moves both.

OP_SEND_ACTION_GOAL = UpstreamRef(
    "op=send_action_goal {id, action, action_type, args, feedback}",
    "VERIFIED",
    bridge(_PROTO, 523),
    "protocol 4.5.3; it fails when no action server is advertised for the action or the args "
    "do not fit its goal type (lines 537 to 540). There is no message that says a goal was "
    "accepted",
)
OP_CANCEL_ACTION_GOAL = UpstreamRef(
    "op=cancel_action_goal {id, action}",
    "VERIFIED",
    bridge(_PROTO, 542),
    "protocol 4.5.4; the id is the goal's, and a goal that has already completed cannot be "
    "cancelled (line 557)",
)
OP_ACTION_FEEDBACK = UpstreamRef(
    "op=action_feedback {id, action, values}", "VERIFIED", bridge(_PROTO, 559), "protocol 4.5.5"
)
OP_ACTION_RESULT = UpstreamRef(
    "op=action_result {id, action, values, status, result}",
    "VERIFIED",
    bridge(_PROTO, 575),
    "protocol 4.5.6; status is action_msgs/GoalStatus's integer, and result false carries a "
    "string in values (line 584)",
)
ACTIONS_SINCE = UpstreamRef(
    "rosbridge_library 2.0.0 has actions",
    "VERIFIED",
    bridge(_CHANGELOG, 103),
    "'Support actions in rosbridge protocol (#886)' in 2.0.0 (2024-10-08, line 91), the same "
    "entry on the humble branch at fc1a7af (line 88) and the jazzy branch at cb33a16 (line 101). "
    "A bridge from before 2.0.0 has no actions at all, and a goal sent to one never answers",
)
GOAL_THREAD_PARAM = UpstreamRef(
    "send_action_goals_in_new_thread",
    "VERIFIED",
    bridge(_SEND_GOAL, 67),
    "the capability's own default is False. Unless it is true, a goal is run to its end on the "
    "thread that reads every message from that client (lines 76 to 86, GOAL_RUNS_ON_THE_READER); "
    "a cancel is always given a thread of its own (lines 88 to 92), but only once that reader "
    "reaches it, after the goal has ended (rosbridge_suite issue #909)",
)
GOAL_RUNS_ON_THE_READER = UpstreamRef(
    "IncomingQueue",
    "VERIFIED",
    bridge(_HANDLER, 81),
    "one thread per client takes its messages one at a time (protocol.incoming at line 122), and "
    "send_action_goal blocks it in client_handler.run() (send_action_goal.py line 152) when the "
    "goal is not given a thread of its own",
)
GOAL_THREAD_DEFAULTS = UpstreamRef(
    "send_action_goals_in_new_thread defaults by branch",
    "VERIFIED",
    bridge(_LAUNCH, 26),
    "true in the launch file and the node (rosbridge_websocket.py line 101) on the ros2 branch; "
    "false on humble at fc1a7af (launch line 23, node line 107, which warns at startup that "
    "'sending action goals will block the main thread', lines 283 to 289); true on jazzy at "
    "cb33a16 (launch line 25, node line 110)",
)
BRIDGE_NODE_PARAMS = UpstreamRef(
    "/rosbridge_websocket",
    "VERIFIED",
    bridge(_NODE, 130),
    "the node's name; it declares each of its protocol parameters as a read-only ROS parameter "
    "(lines 155 to 163) and the protocol runs on the parameter's value (lines 166 to 168), so "
    "/rosapi/get_param on /rosbridge_websocket:send_action_goals_in_new_thread asks the bridge "
    "itself; the stock launch names the node so (launch line 38), under a namespace argument "
    "that defaults to none",
)
GOAL_DROPPED_BY_GLOB = UpstreamRef(
    "actions_glob",
    "VERIFIED",
    bridge(_SEND_GOAL, 119),
    "a goal to an action the glob does not match is dropped with a warning in the bridge's log "
    "and no action_result at all (lines 119 to 124), so a client needs a deadline of its own",
)
NO_ACTION_SERVER = UpstreamRef(
    "No action server available",
    "VERIFIED",
    bridge(_ACTIONS, 206),
    "the bridge waits a second for the server (server_timeout_time, line 154) and answers "
    "result false with this string (lines 206 to 208)",
)
GOAL_REJECTED = UpstreamRef(
    "Action goal was rejected",
    "VERIFIED",
    bridge(_ACTIONS, 175),
    "a server's refusal (lines 174 to 177) reaches the client as result false with this string "
    "and status 0 (send_action_goal.py _failure, lines 195 to 211); the server's reason for it "
    "never crosses the bridge",
)
RESULT_TRUE_WHATEVER_STATUS = UpstreamRef(
    "result true carries the goal's own status",
    "VERIFIED",
    bridge(_SEND_GOAL, 175),
    "a goal that ran is result true with the server's status and result values, whether it "
    "SUCCEEDED, was CANCELED or ABORTED (_success, lines 175 to 193): a client reads status and "
    "the action's own error_code",
)
CANCEL_WHILE_RUNNING = UpstreamRef(
    "a cancel reaches a goal the bridge is still running",
    "VERIFIED",
    bridge(_SEND_GOAL, 170),
    "cancel_action_goal cancels only an id still in client_handler_list (lines 157 to 173); the "
    "goal's own result is what answers it",
)
CLOSED_CLIENT_CANCELS_NOTHING = UpstreamRef(
    "a client that goes cancels nothing",
    "VERIFIED",
    bridge(_CAPABILITY, 87),
    "Capability.finish() is empty (lines 87 to 92) and SendActionGoal does not override it; the "
    "socket's close finishes the queue and nothing else (websocket_handler.py lines 194 to 208), "
    "so a goal sent goes on to its end",
)
SVC_ACTION_SERVERS = UpstreamRef(
    "/rosapi/action_servers",
    "VERIFIED",
    bridge(_ROSAPI_NODE, 119),
    "create_service(GetActionServers, '~/action_servers'); the handler lists each name that has "
    "both an _action/feedback and an _action/status topic (lines 297 to 307, proxy.py "
    "filter_action_servers at line 248)",
)
SRV_ACTION_SERVERS = UpstreamRef(
    "rosapi_msgs/srv/GetActionServers",
    "VERIFIED",
    bridge("rosapi_msgs/srv/GetActionServers.srv", 3),
    "no request fields; {string[] action_servers}",
)
ACTION_CLIENT = UpstreamRef(
    "roslibpy.ActionClient(ros, name, action_type, reconnect_on_close=True)",
    "VERIFIED",
    src(_CORE, 550),
    "the constructor of the class at line 541; reconnect_on_close is stored and nothing in the "
    "class reads it, so a goal does not outlive a reconnect",
)
ACTION_SEND_GOAL = UpstreamRef(
    "ActionClient.send_goal(goal, resultback, feedback, errback)",
    "VERIFIED",
    src(_CORE, 560),
    "returns the goal's id at once and never blocks, and always asks for feedback "
    "('feedback': True, line 587); the id is what cancel_goal takes (line 604)",
)
ACTION_CANCEL_GOAL = UpstreamRef(
    "ActionClient.cancel_goal(goal_id)",
    "VERIFIED",
    src(_CORE, 606),
    "sends cancel_action_goal and expects the goal's result to answer it; its own docstring says "
    "async cancelation is not yet supported on rosbridge, issue #909 (line 609)",
)
ACTION_GOAL = UpstreamRef("roslibpy.Goal(values)", "VERIFIED", src(_CORE, 172), "a UserDict")
GOAL_STATUS = UpstreamRef(
    "roslibpy.GoalStatus",
    "VERIFIED",
    src(_CORE, 158),
    "an Enum of action_msgs' statuses, UNKNOWN 0 to ABORTED 6 (lines 163 to 169)",
)
ACTION_RESULT_HANDLER = UpstreamRef(
    "resultback({status, values}) and errback({status, values})",
    "VERIFIED",
    src(_COMM, 177),
    "the result's status is read as GoalStatus(status), which raises for a number outside it and "
    "the message is then skipped (comm_autobahn.py lines 35 to 45); result false goes to errback "
    "(lines 179 to 181). A feedback after its result unpacks None and is skipped the same way "
    "(lines 152 to 153), and an op of status has no handler (line 41)",
)
ONE_REACTOR = UpstreamRef(
    "reactor.callFromThread(reactor.stop)",
    "VERIFIED",
    src(_AUTOBAHN, 281),
    "Ros.terminate() stops Twisted's reactor (lines 278 to 284), the one module level reactor "
    "every roslibpy client in a process runs on (line 12), started once on a daemon thread "
    "(lines 186 to 191): ending one client so ends every client's socket",
)
QUEUED_UNTIL_READY = UpstreamRef(
    'self.once("ready", callback)',
    "VERIFIED",
    src(_AUTOBAHN, 99),
    "a send handed over while no socket is up waits for the next ready (lines 95 to 99), an action "
    "goal among them (ros.py lines 291 to 307), and the factory reconnects after any close the "
    "client did not ask for (lines 109 to 114), so what was queued goes out once the bridge is "
    "back",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
