"""The Models tab's backend: what model versions are live, the drift reports people have filed, the
retraining queue, and the Admin actions on them (approve/cancel a retraining job, promote or roll
back a model version).

A third feature module alongside app/chat/ and app/workflow/, with the same rule: it imports only
app/shared/. The tables and the rules that govern them are in app/shared/ (db/models/modelops.py,
modelops/) because the chat monitoring agent writes to the same records; the inference service is
reached through app/shared/inference. tests/test_module_boundaries.py enforces the isolation.
"""
