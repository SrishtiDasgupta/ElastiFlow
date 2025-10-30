// mpi_example.cpp
#include <mpi.h>
#include <iostream>

int main(int argc, char** argv) {
    // Initialize the MPI environment
    MPI_Init(&argc, &argv);

    // Get the number of processes
    int world_size;
    MPI_Comm_size(MPI_COMM_WORLD, &world_size);

    // Get the rank of the process
    int world_rank;
    MPI_Comm_rankd(MPI_COMM_WORLD, &world_rank);

    // Ensure the script is running with exactly 8 slots
    if (world_size != 8) {
        if (world_rank == 0) {  // Only the root process prints the error
            std::cerr << "This script requires exactly 8 MPI slots." << std::endl;
        }
        MPI_Finalize();
        return -1;
    }

    // Each process will print its rank and the total number of processes
    std::cout << "Process " << world_rank << " out of " << world_size << " is running." << std::endl;

    // Gather data from all processes at the root process
    int* gathered_data = nullptr;
    if (world_rank == 0) {
        gathered_data = new int[world_size];
    }

    MPI_Gather(&world_rank, 1, MPI_INT, gathered_data, 1, MPI_INT, 0, MPI_COMM_WORLD);

    // Root process prints the gathered data
    if (world_rank == 0) {
        std::cout << "Gathered data from all processes: ";
        for (int i = 0; i < world_size; i++) {
            std::cout << gathered_data[i] << " ";
        }
        std::cout << std::endl;
        delete[] gathered_data;
    }

    // Finalize the MPI environment
    MPI_Finalize();

    return 0;
}