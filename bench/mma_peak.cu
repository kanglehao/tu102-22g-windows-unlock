// TU102 HMMA peak probe - read-only measurement, no register configuration touched.
// Compares FP16->FP32 accumulate (the path cut in half on consumer Turing)
// against FP16->FP16 accumulate (not cut, serves as a measurement control).
// Pure register mma.sync loop: VRAM footprint < 1 MB, no cuBLAS/cuDNN/torch.

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cuda_runtime.h>

#define GROUPS 8

__global__ void k_f32acc(float* out, long long iters) {
    unsigned a0 = 0u, a1 = 0u, b0 = 0u;
    float c[GROUPS][4];
    #pragma unroll
    for (int i = 0; i < GROUPS; ++i) {
        c[i][0] = 0.f; c[i][1] = 0.f; c[i][2] = 0.f; c[i][3] = 0.f;
    }

    for (long long it = 0; it < iters; ++it) {
        #pragma unroll
        for (int i = 0; i < GROUPS; ++i) {
            asm volatile(
                "mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 "
                "{%0,%1,%2,%3}, {%4,%5}, {%6}, {%0,%1,%2,%3};\n"
                : "+f"(c[i][0]), "+f"(c[i][1]), "+f"(c[i][2]), "+f"(c[i][3])
                : "r"(a0), "r"(a1), "r"(b0));
        }
    }
    float s = 0.f;
    #pragma unroll
    for (int i = 0; i < GROUPS; ++i) s += c[i][0] + c[i][1] + c[i][2] + c[i][3];
    if (__int_as_float(__float_as_int(s) ^ 0x7fffffff) == 1234.5678f) out[0] = s;
}

__global__ void k_f16acc(float* out, long long iters) {
    unsigned a0 = 0u, a1 = 0u, b0 = 0u;
    unsigned c[GROUPS][2];
    #pragma unroll
    for (int i = 0; i < GROUPS; ++i) { c[i][0] = 0u; c[i][1] = 0u; }

    for (long long it = 0; it < iters; ++it) {
        #pragma unroll
        for (int i = 0; i < GROUPS; ++i) {
            asm volatile(
                "mma.sync.aligned.m16n8k8.row.col.f16.f16.f16.f16 "
                "{%0,%1}, {%2,%3}, {%4}, {%0,%1};\n"
                : "+r"(c[i][0]), "+r"(c[i][1])
                : "r"(a0), "r"(a1), "r"(b0));
        }
    }
    unsigned s = 0u;
    #pragma unroll
    for (int i = 0; i < GROUPS; ++i) s += c[i][0] + c[i][1];
    if ((s ^ 0xffffffffu) == 1234u) out[0] = (float)s;
}

static const double FLOPS_PER_MMA = 2048.0;

static double run(int dev, int which, long long iters, int blocks, int warps) {
    float* d_out = nullptr;
    cudaSetDevice(dev);
    cudaMalloc(&d_out, sizeof(float));

    dim3 grid(blocks), block(32 * warps);
    cudaEvent_t e0, e1;
    cudaEventCreate(&e0); cudaEventCreate(&e1);

    if (which == 0) k_f32acc<<<grid, block>>>(d_out, 2000000LL);
    else            k_f16acc<<<grid, block>>>(d_out, 2000000LL);
    cudaDeviceSynchronize();

    cudaEventRecord(e0);
    if (which == 0) k_f32acc<<<grid, block>>>(d_out, iters);
    else            k_f16acc<<<grid, block>>>(d_out, iters);
    cudaEventRecord(e1);
    cudaError_t err = cudaDeviceSynchronize();
    if (err != cudaSuccess) printf("    CUDA error: %s\n", cudaGetErrorString(err));
    cudaEventSynchronize(e1);

    float ms = 0.f;
    cudaEventElapsedTime(&ms, e0, e1);
    double total_mma = (double)blocks * (double)warps * (double)iters * (double)GROUPS;
    double tflops = total_mma * FLOPS_PER_MMA / (ms * 1e-3) / 1e12;

    cudaEventDestroy(e0); cudaEventDestroy(e1);
    cudaFree(d_out);
    printf("    %-30s %8.1f ms   %7.2f TFLOPS\n",
           which == 0 ? "FP16->FP32 acc (HMMA.F32)" : "FP16->FP16 acc (control)",
           ms, tflops);
    return tflops;
}

int main(int argc, char** argv) {
    int dev = (argc > 1) ? atoi(argv[1]) : 0;
    int blocks = 136;
    int warps = 4;
    long long iters = 6000000LL;

    cudaDeviceProp p{};
    cudaGetDeviceProperties(&p, dev);
    int clk = 0;
    cudaDeviceGetAttribute(&clk, cudaDevAttrClockRate, dev);
    printf("GPU %d: %s | SMs=%d | clock=%.0f MHz | sm_%d%d\n",
           dev, p.name, p.multiProcessorCount, clk / 1000.0, p.major, p.minor);

    double f32a = run(dev, 0, iters, blocks, warps);
    double f16a = run(dev, 1, iters, blocks, warps);
    printf("    --> f32acc/f16acc = %.3f   [cut in half ~0.50 | full speed ~1.00]\n",
           f32a / f16a);
    return 0;
}
