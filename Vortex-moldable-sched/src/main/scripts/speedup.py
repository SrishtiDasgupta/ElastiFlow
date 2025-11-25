import numpy as np

tinyDaOverhead = {1000: 4.713, 750: 8.378, 500: 25.799}
 
def getRuntime(nodes, mesh, instance):
    if nodes == 0: return 0
    runtime = 0
    match instance:
        case 'on-prem':
            # return getSuperMUCRuntime(nodes, mesh)
            runtime = hpcOnpremRuntime(nodes, mesh)        
        case 'hpc7a.24xlarge':
            runtime = getHPC24xRuntime(nodes, mesh)
        case 'hpc7a.12xlarge':
            runtime = getHPC12xRuntime(nodes, mesh)
        case 'c7i.24xlarge':
            runtime = getC7i24xRuntime(nodes, mesh)
        case 'c7i.12xlarge':
            runtime = getC7i12xRuntime(nodes, mesh)
        case 'c6i.32xlarge':
            runtime = getC6i32xRuntime(nodes, mesh)
        case 'c6i.16xlarge':
            runtime = getC6i16xRuntime(nodes, mesh)
        
    return runtime + tinyDaOverhead[mesh]
 
def getSuperMUCRuntime(nodes, mesh):
    a = 1.61212019e+04
    b = -4.87658705e+00
    c = -4.96047281e+00  
    d = 6.26627614e+01
    return a * np.exp(b * nodes/8.0 + c * mesh/1000.0) + d
 
def hpcOnpremRuntime(nodes, mesh):
    runtimes1 = {1000: 109, 750: 149, 500: 469.41}
    runtimes2 = {1000: 64.01, 750: 87.09, 500: 245.7}
    
    match nodes:
        case 1:
            return runtimes1[mesh]
        case 2:
            return runtimes2[mesh]
        case _:
            a = 1.45170288e+04
            b = -2.52000045e+00
            c = -5.90571174e+00
            d = 5.67339488e+01
            return a * np.exp(b * nodes/8.0 + c * mesh/1000.0) + d 
 
def getHPC24xRuntime(nodes, mesh):
    a = 1.81359361e+05
    b = -1.98667306e+00
    c = -1.06079896e+01  
    d = 1.35386955e+02
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d
 
def getHPC12xRuntime(nodes, mesh):
    a = 3.21775266e+04
    b = -2.02276928e+00
    c = -5.67891714e+00
    d = 1.15310405e+02
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d
 
def getC7i24xRuntime(nodes, mesh):
    a = 1.05844099e+05
    b = -1.57854496e+00
    c = -7.98343782e+00
    d = 7.57525699e+01
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d
 
def getC7i12xRuntime(nodes, mesh):
    a = 7.95643368e+04
    b = -9.63999756e-01
    c = -6.76799751e+00
    d = 1.41135748e+02
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d
 
def getC6i32xRuntime(nodes, mesh):
    a = 9.10912989e+04
    b = -1.63962907e+00
    c = -7.82892030e+00
    d = 7.88640796e+01
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d
 
def getC6i16xRuntime(nodes, mesh):
    a = 1.31628128e+05
    b = -1.52667352e+00
    c = -7.88895131e+00
    d = 9.58218540e+01
    return a * np.exp(b * nodes/4.0 + c * mesh/1000.0) + d

if __name__ == "__main__":
    print(hpcOnpremRuntime(1, 1000))
