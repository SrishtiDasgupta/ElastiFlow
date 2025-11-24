class Variable():
    def __init__(self, id, value=None):
        self.id: str = id
        self.subscribers = [] # Notify functions

        # All variables are implicitly lists (if they are not, elements can be appended through e.g. yieldToInput)
        match value:
            case list():
                self.value_list = value
            case None:
                self.value_list = []
            case _:
                self.value_list = [value]

    def subscribe(self, fn):
        self.subscribers.append(fn)
    
    def append(self, value):
        self.value_list.append(value)
        for subscriber in self.subscribers:
            subscriber()
    
    def toString(self):
        return self.id + ": " + str(self.value_list)
    
    # Returns the first value by default and removes it from the list
    def getValue(self, index = 0):
        val = None
        if self.value_list:
            val = self.value_list[index]
            self.value_list = self.value_list[index+1:]
        return val