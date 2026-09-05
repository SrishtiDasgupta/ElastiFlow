from .steep_actions import *
from .steep_variables import Variable

# Implemented with the gracious help of ChatGPT

# Note: we do not adhere to any scoping guidlines; if a variable name is used once, it is always global!!
"""
 Q: Why is yaml parsed here and not in the seperate classes
 A: Short answer is Variables. As they are used for linking, different actions should
    access the same variable object. This is ensured by the object_registry
"""
class Steep_Parser():

    def __init__(self, data):
        # Initialize a global registry
        self.object_registry = {}

        # create variables
        self.variables = []
        for variable_data in data["vars"]:
            value = None
            if "value" in variable_data and variable_data["value"] is not None:
                value = variable_data["value"]
            
            variable = Variable(variable_data["id"], value=value)
            self.variables.append(variable)
            self.object_registry[variable_data["id"]] = variable

        self.actions = []
        for action in data["actions"]:
            self.actions.append(self.create_action(action, data['id']))

    def getTuple(self):
        return (self.variables, self.actions)


    def get_variable(self, id):
        if not id:
            return None
        if id in self.object_registry:
            return self.object_registry[id]
        
        variable = Variable(id)
        self.object_registry[id] = variable
        return variable


    # Currently (partially) supports ForEachActions and ExecuteActions
    def create_action(self, data, wf_id):
        match data["type"]:
            case "for":
                # required properties
                input_parameter = self.get_variable(data["input"])
                enumerator = self.get_variable(data["enumerator"])

                # optional properties
                output_parameter = None
                if "output" in data:
                    output_parameter = self.get_variable(data["output"])
                
                actions = []
                if "actions" in data:
                    for action_data in data["actions"]:
                        action = self.create_action(action_data, wf_id)
                        actions.append(action)
                
                yieldToInput = self.get_variable(data.get("yieldToInput", None))
                
                return ForEachAction(wf_id, input_parameter, enumerator, output_parameter=output_parameter, yieldToInput=yieldToInput, actions=actions)

            case "execute":
                service =  data["service"]
                
                # all other attributes are optional
                input_parameters = []
                if "inputs" in data:
                    for input in data["inputs"]:
                        input_parameter = self.get_variable(input["var"])
                        input_parameters.append(input_parameter)

                # Note: We only allow 1 output at present, although it is a list
                output_parameters = []
                if "outputs" in data:
                    for output in data["outputs"]:
                        output_parameter = self.get_variable(output["var"])
                        output_parameters.append(output_parameter)
                
                return ExecuteAction(wf_id, service, input_parameters, output_parameters)


            case _:
                raise KeyError