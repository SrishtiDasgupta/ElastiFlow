import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score

nodes = np.array([1, 2, 4])
meshes = np.array([1000, 750, 500])

# SuperMUC data
supermuc = {
    1000: np.array([120+44.35, 60+43.8242, 48.8099, 27.3107]),
    750: np.array([240+9.0629, 180+10.3067, 60+14.1784, 38.3804]),
    500: np.array([(13*60) + 37.0609, 360+53.6984, 180+49.5384, 120+6.38])
}
# HPC data
hpc12x = {
    1000: np.array([180+59.547, 120+47.0197, 60+32.7233]),
    750: np.array([360+40.5276, 180+50.2836, 120+18.9786]),
    500: np.array([21*60 + 17.7992, 12*60 + 6.1492, 6*60 + 92.88])
}
hpc24x = {
    1000: np.array([120+9.7587, 60+58.83335, 60+24.34855]),
    750: np.array([180+36.8668, 120+25.8152, 60+0.6822]),
    500: np.array([660+29.9875, 7*60 + 42.61635, 4*60 + 3.0312]),
    250: np.array([135*60 + 12.2362, 73*60 + 9.9257, 38*60 + 44.431])
}
# c7i data
c7i12x = {
    1000: np.array([240+13, 180+59, 120+26.9]),
    750: np.array([9*60+17, 420+19, 240+8.54]),
    500: np.array([38*60+1.28, 29*60+18.266, 20*60+12.86]),
}
c7i24x = {
    1000: np.array([120+13.0353, 60+29.1069, 55.3941]),
    750: np.array([240+27.2547, 120+56.5539, 60+53.5845]),
    500: np.array([23*60+57.2645, 14*60+22.8395, 9*60+17.7081]),
}
# c6i data
c6i32x = {
    1000: np.array([120+21.97, 60+35.87, 57.1997]),
    750: np.array([240+21.2746, 120+50.3549, 60+46.2611]),
    500: np.array([21*60 + 58.8517, 13*60 + 20.5707, 8*60 + 17.2505]),
}
c6i16x = {
    1000: np.array([120+50.1109, 60+55.5734, 60+9.9352]),
    750: np.array([300+59.2595, 180+52.667, 120+23.4539]),
    500: np.array([31*60+54.0422, 18*60+27.4285, 13*60+3.2357]),
}

fig, axs = plt.subplots(1, 2, figsize=(14, 6))


# x = nodes, y = meshes, z = runtimes
# a * exp(b * N) + c * M + d
def exp_func_mesh(X, a, b, c, d):
    x, y = X
    return a * np.exp(b * x + c * y) + d

def exp_func(x, a, b, c):
    return a * np.exp(b * x) + c

# aln(x) + bln(y) + c
def log_func_mesh(X, a, b, c):
    x, y = X
    return a * np.log(x) + b * np.log(y) + c

def log_func(x, a, b):
    return a * np.log(x) + b

# ax^2 + by^2 + cxy + dx + ey + f 
def poly_func_mesh(X, a, b, c, d, e, f):
    x, y = X
    return a*(x**2) + b*(y**2) + c*x*y + d*x + e*y + f

# ax^2 + bx + c
def poly_func(x, a, b, c):
    return a * (x**2) + b * x + c

# ax + bxlogx / y^c + d
def amdahl_func_mesh(X, a, b, c, d):
    x, y = X
    return a*x + (b*x*np.log(x) / y**c) + d

# a (b + (1-b / x)) + c
def amdahl_func(x, a, b, c):
    return a * (b * ((1-b) / x)) + c

def fitWithoutMesh(func, initial):
    for mesh in meshes:
        y = runtimes[mesh]
        params, _ = curve_fit(func, nodes, y, p0=initial, maxfev=5000)
        preds = func(nodes, *params)
        r2 = r2_score(y, preds)
        print(f'{mesh}: {params}, R2: {r2:.4f}')

        x_smooth = np.linspace(1, 8, 100)
        axs[1].scatter(nodes, y, color='black')
        axs[1].plot(x_smooth, func(x_smooth, *params), label=f'{mesh}: {r2:.4f}')

    axs[1].set_title('Without Mesh')
    axs[1].grid(True)

def fitMesh(func, initial=None, ind=0, normalize = False):
    N_vals = np.tile(nodes, len(meshes))
    M_vals = np.repeat(meshes, len(nodes))
    # Normalize for Exponential fit
    if normalize:
        M_vals = M_vals / max(meshes)
        N_vals = N_vals / max(nodes)
    y_vals = np.concatenate((runtimes[1000], runtimes[750], runtimes[500]))
    params, _ = curve_fit(func, (N_vals, M_vals), y_vals, p0=initial, maxfev=5000)
    pred = func((N_vals, M_vals), *params)
    r2 = r2_score(y_vals, pred)
    print(f'With mesh: {params}, R2: {r2:.4f}')
    
    for mesh in meshes:
        y = runtimes[mesh]
        x_smooth = np.linspace(1, 8, 100)
        axs[ind].scatter(nodes, y, color='black')
        if normalize:
            preds = func((x_smooth/max(nodes), np.full_like(x_smooth, mesh/1000.0)), *params)
        else:
            preds = func((x_smooth, np.full_like(x_smooth, mesh)), *params)
        axs[ind].plot(x_smooth, preds, label=f'{mesh}: {r2:.4f}')
    
    axs[ind].grid(True)

if __name__ == "__main__":
    runtimes = c6i16x
    axs[0].set_title('With mesh')

    # fitMesh(exp_func_mesh, initial=[1000, -1, -1, 100], ind=0, normalize=True)
    # fitWithoutMesh(exp_func, [250, -1, 1])
    # fitMesh(log_func_mesh, initial=None)
    # fitWithoutMesh(log_func, initial=None)
    # fitMesh(poly_func_mesh, initial=None)
    # fitWithoutMesh(poly_func, initial=None)
    # fitMesh(amdahl_func_mesh, initial=[0.01, 0.8, 0.1, 0])
    # fitWithoutMesh(amdahl_func, initial=[120, 0.8, 0])

    plt.tight_layout()
    fig.supxlabel('No. of instances')
    fig.supylabel('Runtimes')
    fig.suptitle('Seissol hpc7a.12xlarge runtimes with Exp fit')
    fig.legend()
    plt.savefig(f'src/main/plots/speedup.png')

