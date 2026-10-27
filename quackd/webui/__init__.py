"""The page `quackd ui` serves: quackd in a browser, on a desktop or a phone.

The page drives quackd's own commands, `quackd run` for a goal and `quackd drive` for a chat,
each as a child process it talks to over JSON lines (`quackd.front`, ADR-0069). Every module
here but the Streamlit pages is plain Python the suite tests without Streamlit installed:

- `options.py` reads every option off the commands themselves, and builds the command a set of
  choices makes;
- `catalog.py` is what the pickers offer: robots, pilots, policies, decision LLMs, keys;
- `jobs.py` starts, watches and stops the children, one robot at a time;
- `probe.py` checks a robot's connection in a child of its own, moving nothing.

Streamlit is imported by the pages alone, and only once `quackd ui` has started them, which
refuses without the `ui` extra.
"""
