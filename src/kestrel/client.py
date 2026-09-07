"""Public local client. No HTTP service or live model provider."""
from pathlib import Path

from .application import Lab
from .experiments import Experiments
from .integration import Connections


class Client:
    def __init__(self, lab: str | Path):
        self.lab = Lab(Path(lab))
        self.projects = Connections(self.lab.projects)
        self.experiments = Experiments(self.lab)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.lab.close()

    def describe_project(self, identity):
        return self.projects.describe(identity)

    def preview_snapshot(self, identity):
        return self.projects.preview(identity)

    def propose_experiment(self, snapshot, operation, parameters=None, edits=None, limits=None):
        return self.experiments.propose(snapshot, operation, parameters, edits, limits)

    def inspect(self, identity):
        return self.experiments.inspect(identity)

    status = inspect

    def run(self, identity, approval):
        return self.experiments.run(identity, approval)

    def cancel(self, identity):
        return self.experiments.cancel(identity)

    def artifact_list(self, identity):
        return self.experiments.artifacts(identity)
