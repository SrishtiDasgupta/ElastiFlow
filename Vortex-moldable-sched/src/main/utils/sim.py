import time

def getTime(sim):
    return sim.now if sim else time.time()

def removeElement(mb, queue):
    if mb:
        mb.retrieve(isall=False)
    else:
        queue.pop()
    
def peekElement(mb, queue):
    return mb.peek() and mb.peek()[0] if mb else queue.peek()

def getAllElements(mb, queue, count):
    if mb:
        all_elements = mb.peek()
        n = len(all_elements)
        if not count or n <= count:
            return mb.retrieve(isall=True)
        else:
            out = mb.peek()[:count]
            for i in range(count): mb.retrieve(isall=False)
            return out  
    else:
        return queue.pop(count or queue.getLength())
