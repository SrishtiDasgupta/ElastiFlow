import argparse
import itertools
import multiprocessing as mp
import numpy as np
import os
import time
import umbridge

if __name__ == "__main__":
        parser = argparse.ArgumentParser()
        parser.add_argument("port", help="port", type=int)
        args = parser.parse_args()
        
        address = f"http://localhost:{args.port}"
        server_available = False
        while not server_available:
                try:
                        model = umbridge.HTTPModel(address, "forward")
                        print("Server available")
                        server_available = True
                except:
                        print("Server not available")
                        time.sleep(10)

        def eval_um_model(cohesion, order=4):
                np.random.seed(int.from_bytes(os.urandom(4), byteorder='little'))
                wait_time = np.random.rand() * 10
                print(f"Delay submission by {wait_time} seconds.")
                time.sleep(wait_time)
                
                config = {"order": order}
                #result = model([[traction_left * 1e6, traction_middle * 1e6, traction_right * 1e6]], config)
                result = model([[cohesion]], config)
                print(result)
                return result

        start_time = time.time() 
        #tractions_middle = [81.2, 81.3, 81.4, 81.5, 81.6, 81.7, 81.8, 81.9, 82.0, 82.1]
        #tractions_left = [78.0, 79.0, 80.0]
        #tractions_right = [61.0, 62.0, 63.0]
        #arguments = [a for a in itertools.product(tractions_left, tractions_middle, tractions_right)]
        cohesions = [0.0e6, 1.0e6, 3.0e6, 5.0e6, 7.0e6]
        orders = [4, 5, 6]
        arguments = [a for a in itertools.product(cohesions, orders)]
        number_of_models = len(arguments)
        print(f"Evaluate {number_of_models} in parallel")
        with mp.Pool(10) as p:
                result = p.starmap(eval_um_model, arguments)
                print(result)
        end_time = time.time()
        print(end_time - start_time)

