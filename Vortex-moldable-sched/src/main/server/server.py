from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import time
import yaml

class RequestHandler(BaseHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = yaml.safe_load(post_data.decode('utf-8'))
        if data:
            data['submit_time'] = time.time()
            wf_id = data.get('id', '?')
            print(f"Workflow received at {data['submit_time']} (id={wf_id})")
            if RequestHandler.queue is not None:
                result = RequestHandler.queue.push(str(data))
                if result is None:
                    print(f"[ERROR] queue.push returned None for wf {wf_id} — wf LOST!")
                else:
                    print(f"[OK] Pushed wf {wf_id} to wf_queue (queue len now {result})")
            else:
                print(f"[ERROR] RequestHandler.queue is None — wf {wf_id} LOST!")
        else:
            print(f"[WARN] Empty POST body received on wf port")
        sendResponse(self, data)   # always respond, even on empty data

class FinishJobHandler(BaseHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = yaml.safe_load(post_data.decode('utf-8'))

        if FinishJobHandler.queue is not None:
            FinishJobHandler.queue.push(str(data))
        
        sendResponse(self, data)


class ResourceRequestHandler(BaseHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = yaml.safe_load(post_data.decode('utf-8'))
        if data:
            data['client-ip'] = self.client_address[0]
            data['request-time'] = time.time()

        if ResourceRequestHandler.queue is not None:
            ResourceRequestHandler.queue.push(str(data))

        sendResponse(self, data)

def run(queue, server_class=HTTPServer, handler_class=RequestHandler, port=8080):
    server_address = ('', port)
    handler_class.queue = queue
    httpd = server_class(server_address, handler_class)
    print(f'Starting server on port {port}...')
    httpd.serve_forever()

def sendResponse(obj: BaseHTTPRequestHandler, data):
    # Respond with a simple JSON response
    obj.send_response(200)
    obj.send_header('Content-type', 'application/json')
    obj.end_headers()
    response = json.dumps({"message": "Data received", "data": data})
    obj.wfile.write(response.encode('utf-8'))