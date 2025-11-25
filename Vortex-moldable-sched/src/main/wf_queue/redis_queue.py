import redis

from wf_queue.abstract_queue import Queue

# Simple Wrapper around the redis api to conform with the queue functionality
class Redis_Queue(Queue):

    def __init__(self, queue_name, host='localhost', port=6379, db=0):
        # Connect to Redis
        self.redis = redis.Redis(host=host, port=port, db=db)
        self.queue_name = queue_name
        self.redis.delete(queue_name) # Delete queue if already present

    def push(self, val):
        return self.redis.rpush(self.queue_name, val)
    
    def pop(self, count=None):
        return self.redis.lpop(self.queue_name, count)
    
    def peek(self):
        return self.redis.lindex(self.queue_name, 0)
    
    def getLength(self):
        return self.redis.llen(self.queue_name)