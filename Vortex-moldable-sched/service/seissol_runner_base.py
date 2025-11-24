from abc import ABC, abstractmethod

# === Abstract Base Class ===
class SeisSolRunner(ABC):
    def __init__(self, args):
        self.args = args

    @abstractmethod
    def run(self):
        pass
