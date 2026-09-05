import time
import redis

from elastiflow.wf_queue.abstract_queue import Queue

# Simple Wrapper around the redis api to conform with the queue functionality
class Redis_Queue(Queue):

    def __init__(self, queue_name, host='localhost', port=6379, db=0):
        # Connect to Redis
        self.host = host
        self.port = port
        self.db = db
        self.redis = redis.Redis(host=host, port=port, db=db)
        self.queue_name = queue_name
        self.redis.delete(queue_name) # Delete queue if already present

    def _retry(self, op):
        """Retry a Redis operation up to 3 times with reconnect on failure."""
        for attempt in range(3):
            try:
                return op()
            except (redis.ConnectionError, redis.TimeoutError) as e:
                print(f'[REDIS] {self.queue_name} connection error (attempt {attempt+1}/3): {e}')
                time.sleep(2)
                try:
                    self.redis = redis.Redis(host=self.host, port=self.port, db=self.db)
                except Exception:
                    pass
        print(f'[REDIS] {self.queue_name} all 3 retries failed, returning None')
        return None

    def push(self, val):
        return self._retry(lambda: self.redis.rpush(self.queue_name, val))

    def pop(self, count=None):
        return self._retry(lambda: self.redis.lpop(self.queue_name, count))

    def peek(self):
        return self._retry(lambda: self.redis.lindex(self.queue_name, 0))

    def getLength(self):
        return self._retry(lambda: self.redis.llen(self.queue_name))