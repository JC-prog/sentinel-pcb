"""Model operations shared by the chat monitoring agent and the Models tab (app/modelops/): the
version registry (versions.py), drift reports (drift.py), retraining tickets (tickets.py) and the
retraining-job rules (jobs.py). Tables are in app/shared/db/models/modelops.py; talking to the
inference service is app/shared/inference. Lives in `shared` because chat and modelops may not
import each other.
"""
