from abc import ABC, abstractmethod

class Queue(ABC):

    @abstractmethod
    def push(self):
        pass

    @abstractmethod
    def pop(self):
        pass

    @abstractmethod
    def peek(self):
        pass
