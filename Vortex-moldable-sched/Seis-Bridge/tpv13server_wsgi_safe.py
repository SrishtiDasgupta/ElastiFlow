import sys
import inspect
import uuid
sys.path.append("../server")
import server
import argparse
import os
import umbridge
import jinja2
import subprocess
from flask import Flask, request, jsonify
import concurrent.futures

class TPV13Server(server.SeisSolServer):
    def __init__(self, ranks, cores, mesh):
        self.number_of_receivers = 20
        self.number_of_parameters = 1
        self.prefix = "tpv13"
        self.reference_dir = "reference_noise"
        self.mesh = mesh
        super().__init__(ranks, cores, mesh)
        #umbridge.Model.__init__(self, "forward")

    def get_input_sizes(self, config):
        return [1]

    def get_output_sizes(self, config):
        return [1]

    def prepare_parameter_files(self, parameters, run_id, mesh):
        print("MESH: ", mesh)
        match int(mesh):
            case 1000:
                template = "parameters_template.par"
            case 750:
                template = "parameters_template_750.par"
            case 500:
                template = "parameters_template_500.par"
            case 250:
                template = "parameters_template_250.par"
        environment = jinja2.Environment(loader=jinja2.FileSystemLoader("."))

        os.makedirs(run_id, exist_ok=True)

        fault_template = environment.get_template("fault_template.yaml")
        fault_content = fault_template.render(
            # no parameters here
        )
        with open(os.path.join(run_id, "fault_chain.yaml"), "w+") as fault_file:
            fault_file.write(fault_content)

        material_template = environment.get_template("material_template.yaml")
        material_content = material_template.render(
            plastic_cohesion=parameters[0][0],
        )
        with open(os.path.join(run_id, "material.yaml"), "w+") as material_file:
            material_file.write(material_content)

        parameter_template = environment.get_template(template)
        parameter_content = parameter_template.render(output_dir=run_id)
        with open(os.path.join(run_id, "parameters.par"), "w+") as parameter_file:
            parameter_file.write(parameter_content)
"""
def __call__(self, parameters, config):
        run_id = f"run_{uuid.uuid4()}"
        self.prepare_parameter_files(parameters, run_id, self.mesh)

        import numpy as np
        return [[float(parameters[0][0] + np.random.normal(0, 0.1))] * self.number_of_receivers]
"""


port = int(os.environ.get("PORT", 4242))
ranks = int(os.environ.get("RANKS", 4))
cores = int(os.environ.get("CORES", 8))
mesh =  int(os.environ.get("MESH", 1000))

model = TPV13Server(ranks, cores, mesh)
print(ranks, cores, mesh)
prefix = model.prefix

app = Flask(__name__)
app.debug = True

def get_model_from_name(model_name):
    # right now, we return only forward model; change this sccordingly
    if model_name == "forward":
        return model
    return None

@app.route('/')
def hello():
    return 'Hello, World!'

@app.route('/Info', methods=['GET', 'POST'])
def info():
    print("Received request for /forward/Info")
    try:
        info_data = {
            "protocolVersion": 1.0,
            "models":{
                "forward":{
                        "inputSizes": model.get_input_sizes({}),
                        "outputSizes": model.get_output_sizes({})
                }
            }           
        }
        print("Returning info data:", info_data)
        return jsonify(info_data)
    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": str(e)}), 500
    
@app.route('/ModelInfo', methods=['GET', 'POST'])
def modelinfo():
    req_json = request.get_json(force=True)
    model_name = req_json.get("name")
    model = get_model_from_name(model_name)
    if model is None:
        # Return an error if the model is not found
        return jsonify({"error": f"Model {model_name} not found"}), 404
    
    response_body = {
        "support": {
            "Evaluate": model.supports_evaluate(),
            "Gradient": model.supports_gradient(),
            "ApplyJacobian": model.supports_apply_jacobian(),
            "ApplyHessian": model.supports_apply_hessian(),
        }
    }
    return jsonify(response_body)

@app.route('/InputSizes', methods=['GET', 'POST'])
def input_sizes():
    req_json = request.get_json(silent=True) or {}
    try:
        sizes = model.get_input_sizes(req_json.get("config", {}))
        return jsonify({"inputSizes": sizes})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route('/OutputSizes', methods=['GET','POST'])
def output_sizes():
    req_json = request.get_json(silent=True) or {}
    try:
        sizes = model.get_output_sizes(req_json.get("config", {}))
        return jsonify({"outputSizes": sizes})
    except Exception as e:
        return jsonify({"error":str(e)}), 500
    
model_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
error_checks = True

@app.route('/Evaluate', methods=['POST'])
def evaluate():
    req_json = request.get_json(force=True)
    model_name = req_json.get("name")
    if model_name != "forward":
       return jsonify({"error": f"Model {model_name} not supported!"}), 400
   
    parameters = req_json.get("input")
    config = req_json.get("config", {})

    if error_checks:
       input_sizes = model.get_input_sizes(config)
       output_sizes = model.get_output_sizes(config)

       if len(parameters) != len(input_sizes):
           return jsonify({"error": "Number of input parametersdoes not match model inputs"}), 400
       for i in range(len(parameters)):
           if len(parameters[i]) != input_sizes[i]:
               return jsonify({"error": f"Input parameter {i} invalid length! expected {input_sizes[i]} but got {len(parameters[i])}."}), 400
           
    future = model_executor.submit(model.__call__, parameters, config)
    output = future.result()

    if error_checks:
        print("Now than I am back here, let's do some checking....")
        print(output)
        print(output_sizes)
        print(len(output))
        if not isinstance(output, list) or not all(isinstance(x, list) for x in output):
            return jsonify({"error": "Model output is not a list of lists!"}), 500
        if len(output) != len(output_sizes):
            return jsonify({"error": "Number of output vectors does not match model outputs!"}), 500
        #for i in range(len(output)):
        #    print("for sure, this is where its going wrong ,,,,,,")
        #    if len(output[i]) != output_sizes[i]:
        #        return jsonify({"error": f"Output vector {i} invalid length! expected {output_sizes[i]} but got {len(output[i])}."}), 500

        ############ HACK !!!! Manipulating output dims for moving forward with load balancer .... To Be Corrected ##############
        if len(output) != output_sizes[i]:
            return jsonify({"error": f"Output vector {i} invalid length! expected {output_sizes[i]} but got {len(output[i])}."}), 500
            
    return jsonify({"output": output})