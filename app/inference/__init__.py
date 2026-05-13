"""Inference subpackage.

Imports are deliberately lazy: the FastAPI app, the static site, and the
non-ML routes all need to keep working even on a machine where torch /
ultralytics / opencv-contrib aren't installed yet.  Each submodule guards
its heavy imports with a try/except that records the failure on the module
so the WebSocket layer can report a clean error to the client.
"""
