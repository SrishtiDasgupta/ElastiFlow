In order to compile mpi-test.cpp you need:

1)  To install an mpi implementation, e.g. Open-MPI
    On Fedora, you can run: sudo dnf install openmpi openmpi-devel

2)  Find where the compiler wrapper is situated
    This can be done with: sudo find / -name mpic++

3)  Finally, compile the test
    /path/to/openmpi/bin/mpic++ mpi-test.cpp -o mpi-test.out
