"""Verbs are the only things an LLM can ask the robot to do.

A registry of named verbs exists so that the LLM's vocabulary, the safety allowlist, the
MCP tool list, and — one day — learned ONNX policies are all the same list. Built-ins map
1:1 to shipped robot behaviours; composites are plain Python over built-ins and perception.
"""
