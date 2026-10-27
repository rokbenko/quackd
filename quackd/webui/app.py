"""The page `quackd ui` serves: Streamlit's entry script.

Started only by `quackd ui` (`launch.serve`), which says where the page listens and whether it
asks for a token; a page started any other way says so and does nothing. Every screen draws the
floating Stop button whenever a run or a chat is going, and nothing on any screen runs a robot in
this process: a run is a child of the job table (`jobs`), driven over JSON lines.
"""

from __future__ import annotations

import hmac

import streamlit as st

from quackd.webui import jobs, launch, widgets
from quackd.webui.screens import chat, goal, history, memory, policies, robots, setup

st.set_page_config(page_title="quackd", layout="wide", initial_sidebar_state="auto")

config = launch.CONFIG
if config is None:
    st.title("quackd")
    st.error("Start this page with `quackd ui`.")
    st.stop()
assert config is not None  # st.stop() ends the script, which the type checker cannot see

if config.token and not st.session_state.get("quackd_token_ok"):
    given = st.query_params.get("token") or ""
    # as bytes: a string compare_digest refuses anything but ASCII, and a stray address would
    # end in a traceback rather than the refusal
    if given and hmac.compare_digest(given.encode("utf-8"), config.token.encode("utf-8")):
        st.session_state["quackd_token_ok"] = True
    else:
        st.title("quackd")
        st.error(
            "This page needs its token. Open the address `quackd ui` printed, which carries it, "
            "or scan its QR code."
        )
        st.stop()
if config.token and st.query_params.get("token") != config.token:
    # back in the address on every run: a page switch drops the query, and a refresh, a copied
    # address or a phone whose session lapsed while it was locked would come back without it
    st.query_params["token"] = config.token

widgets.style()
widgets.sync()
table = jobs.table(config.folder)

with st.sidebar:
    st.caption("Working folder")
    st.code(str(config.folder), language=None)
    running = table.running()
    if running:
        st.caption("Running")
        for job in running:
            st.markdown(f"- {job.kind} `{job.id}`" + (f" on `{job.robot}`" if job.robot else ""))
    else:
        st.caption("Nothing running")
    widgets.phone()

page = st.navigation(
    [
        # the default page is the page's root address: Streamlit gives it no path of its own
        st.Page(setup.screen, title="Setup", icon=":material/tune:", default=True),
        st.Page(goal.screen, title="Goal", icon=":material/flag:", url_path="goal"),
        st.Page(chat.screen, title="Chat", icon=":material/forum:", url_path="chat"),
        st.Page(history.screen, title="Runs", icon=":material/history:", url_path="runs"),
        st.Page(
            robots.screen,
            title="Robots",
            icon=":material/precision_manufacturing:",
            url_path="robots",
        ),
        st.Page(memory.screen, title="Memory", icon=":material/psychology:", url_path="memory"),
        st.Page(
            policies.screen, title="Policies", icon=":material/model_training:", url_path="policies"
        ),
    ],
    position="top",
)
page.run()
widgets.floating_stop()
