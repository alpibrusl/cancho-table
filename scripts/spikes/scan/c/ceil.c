// The ceiling of a CSV scan in C: the same record cut as scripts/spikes/scan/kern.ls, four ways, on the whole
// file in memory. cc -O2 ceil.c -o ceil ; ./ceil FILE VARIANT ROUNDS
//   0 byte loop (a state machine over the bytes)
//   1 SWAR: 8 bytes per step, 64-bit loads, ctz
//   2 stage 1 with 16-byte vectors (SSE2 / NEON): the positions of every ',' '\n' '"' of a 64 KiB block in
//     a position array, then stage 2 walks the tokens by the grammar (the lex-sys kern variant 3)
//   3 as 2 with a block of 64 bytes combined into one 64-bit mask before the positions are extracted
// Every variant answers the same checksum as the lex-sys kernels (31667312 on the 1M row file).
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#if defined(__x86_64__)
#include <emmintrin.h>
#elif defined(__aarch64__)
#include <arm_neon.h>
#endif

typedef int64_t i64;
static i64 cells[64];

static inline i64 emit(i64 *nf, i64 *sum, i64 s, i64 e, i64 q) {
    if (*nf < 8) { cells[3 * *nf] = s; cells[3 * *nf + 1] = e; cells[3 * *nf + 2] = q; }
    (*nf)++;
    *sum += e - s + q;
    return 0;
}

static i64 v0(const unsigned char *t, i64 n) {
    i64 p = 0, sum = 0, recs = 0;
    while (p < n) {
        i64 nf = 0; int more = 1;
        while (more) {
            i64 s = p, e = p, q = 0;
            if (p < n && t[p] == '"') {
                i64 i = p + 1; int closed = 0;
                while (!closed && i < n) {
                    if (t[i] == '"') { if (i + 1 < n && t[i + 1] == '"') i += 2; else closed = 1; }
                    else i++;
                }
                s = p + 1; e = i; q = 1; p = i + 1;
            } else {
                i64 i = p;
                while (i < n && t[i] != ',' && t[i] != '\n') i++;
                e = i; p = i;
            }
            emit(&nf, &sum, s, e, q);
            if (p < n && t[p] == ',') p++; else { p++; more = 0; }
        }
        recs++; sum += nf;
    }
    return sum + recs;
}

static inline uint64_t zero_bytes(uint64_t x) { // top bit of each zero byte of x, exactly
    const uint64_t L = 0x7f7f7f7f7f7f7f7fULL;
    return ~(((x & L) + L) | x | L);
}

static i64 v1(const unsigned char *t, i64 n) {
    i64 p = 0, sum = 0, recs = 0;
    while (p < n) {
        i64 nf = 0; int more = 1;
        while (more) {
            i64 s = p, e = p, q = 0;
            if (p < n && t[p] == '"') {
                i64 i = p + 1; int closed = 0;
                while (!closed && i < n) {
                    int hit = 0;
                    while (!hit && i < n) {
                        if (i + 8 <= n) {
                            uint64_t w; memcpy(&w, t + i, 8);
                            uint64_t z = zero_bytes(w ^ 0x2222222222222222ULL);
                            if (!z) i += 8; else { i += __builtin_ctzll(z) >> 3; hit = 1; }
                        } else if (t[i] == '"') hit = 1; else i++;
                    }
                    if (i < n) { if (i + 1 < n && t[i + 1] == '"') i += 2; else closed = 1; }
                }
                s = p + 1; e = i; q = 1; p = i + 1;
            } else {
                i64 i = p; int hit = 0;
                while (!hit && i < n) {
                    if (i + 8 <= n) {
                        uint64_t w; memcpy(&w, t + i, 8);
                        uint64_t z = zero_bytes(w ^ 0x2c2c2c2c2c2c2c2cULL) | zero_bytes(w ^ 0x0a0a0a0a0a0a0a0aULL);
                        if (!z) i += 8; else { i += __builtin_ctzll(z) >> 3; hit = 1; }
                    } else if (t[i] == ',' || t[i] == '\n') hit = 1; else i++;
                }
                e = i; p = i;
            }
            emit(&nf, &sum, s, e, q);
            if (p < n && t[p] == ',') p++; else { p++; more = 0; }
        }
        recs++; sum += nf;
    }
    return sum + recs;
}

#define BLOCK 65536
static i64 pos[BLOCK + 64];

// stage 2 state is kept across blocks
typedef struct { i64 fs, mode, skip, qend, qs, nf, recs, sum; } st_t;

static inline void stage2(const unsigned char *t, i64 n, i64 m, st_t *S) {
    for (i64 j = 0; j < m; j++) {
        i64 at = pos[j]; int c = t[at];
        i64 s = 0, e = 0, q = 0; int emitf = 0;
        if (S->mode == 0) {
            if (c == '"') { if (at == S->fs) { S->mode = 1; S->qs = at + 1; } }
            else { s = S->fs; e = at; emitf = 1; }
        } else if (S->mode == 1) {
            if (c == '"') {
                if (at == S->skip) S->skip = -1;
                else if (at + 1 < n && t[at + 1] == '"') S->skip = at + 1;
                else { S->qend = at; S->mode = 2; }
            }
        } else { s = S->qs; e = S->qend; q = 1; emitf = 1; S->mode = 0; }
        if (emitf) {
            emit(&S->nf, &S->sum, s, e, q);
            S->fs = at + 1;
            if (c == '\n') { S->recs++; S->sum += S->nf; S->nf = 0; }
        }
    }
}

static i64 v2(const unsigned char *t, i64 n) {
    st_t S = {0, 0, -1, 0, 0, 0, 0, 0};
    for (i64 base = 0; base < n; base += BLOCK) {
        i64 end = base + BLOCK < n ? base + BLOCK : n, m = 0, i = base;
        for (; i + 16 <= end; i += 16) {
#if defined(__x86_64__)
            __m128i v = _mm_loadu_si128((const __m128i *)(t + i));
            __m128i k = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(v, _mm_set1_epi8(',')), _mm_cmpeq_epi8(v, _mm_set1_epi8('\n'))), _mm_cmpeq_epi8(v, _mm_set1_epi8('"')));
            unsigned mask = (unsigned)_mm_movemask_epi8(k);
#else
            uint8x16_t v = vld1q_u8(t + i);
            uint8x16_t k = vorrq_u8(vorrq_u8(vceqq_u8(v, vdupq_n_u8(',')), vceqq_u8(v, vdupq_n_u8('\n'))), vceqq_u8(v, vdupq_n_u8('"')));
            // narrow to 4 bits per byte (the usual NEON movemask), 64-bit result
            uint64_t nib = vget_lane_u64(vreinterpret_u64_u8(vshrn_n_u16(vreinterpretq_u16_u8(k), 4)), 0);
            unsigned mask = 0; (void)nib;
            uint64_t mm = nib;
            while (mm) { int b = __builtin_ctzll(mm); pos[m++] = i + (b >> 2); mm &= ~(0xfULL << (b & ~3)); }
            continue;
#endif
            while (mask) { pos[m++] = i + __builtin_ctz(mask); mask &= mask - 1; }
        }
        for (; i < end; i++) { int c = t[i]; if (c == ',' || c == '\n' || c == '"') pos[m++] = i; }
        stage2(t, n, m, &S);
    }
    return S.sum + S.recs;
}

static i64 v3(const unsigned char *t, i64 n) {
    st_t S = {0, 0, -1, 0, 0, 0, 0, 0};
    for (i64 base = 0; base < n; base += BLOCK) {
        i64 end = base + BLOCK < n ? base + BLOCK : n, m = 0, i = base;
        for (; i + 64 <= end; i += 64) {
            uint64_t mask = 0;
#if defined(__x86_64__)
            for (int b = 0; b < 4; b++) {
                __m128i v = _mm_loadu_si128((const __m128i *)(t + i + 16 * b));
                __m128i k = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(v, _mm_set1_epi8(',')), _mm_cmpeq_epi8(v, _mm_set1_epi8('\n'))), _mm_cmpeq_epi8(v, _mm_set1_epi8('"')));
                mask |= (uint64_t)(unsigned)_mm_movemask_epi8(k) << (16 * b);
            }
#else
            // NEON: 64 bytes -> 64-bit mask with the "shift right and insert" trick (simdjson style)
            uint8x16_t b0 = vld1q_u8(t + i), b1 = vld1q_u8(t + i + 16), b2 = vld1q_u8(t + i + 32), b3 = vld1q_u8(t + i + 48);
            #define K(v) vorrq_u8(vorrq_u8(vceqq_u8(v, vdupq_n_u8(',')), vceqq_u8(v, vdupq_n_u8('\n'))), vceqq_u8(v, vdupq_n_u8('"')))
            const uint8x16_t bit = {1,2,4,8,16,32,64,128,1,2,4,8,16,32,64,128};
            uint8x16_t s0 = vandq_u8(K(b0), bit), s1 = vandq_u8(K(b1), bit), s2 = vandq_u8(K(b2), bit), s3 = vandq_u8(K(b3), bit);
            uint8x16_t p0 = vpaddq_u8(s0, s1), p1 = vpaddq_u8(s2, s3);
            p0 = vpaddq_u8(p0, p1); p0 = vpaddq_u8(p0, p0);
            mask = vgetq_lane_u64(vreinterpretq_u64_u8(p0), 0);
#endif
            while (mask) { pos[m++] = i + __builtin_ctzll(mask); mask &= mask - 1; }
        }
        for (; i < end; i++) { int c = t[i]; if (c == ',' || c == '\n' || c == '"') pos[m++] = i; }
        stage2(t, n, m, &S);
    }
    return S.sum + S.recs;
}

int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *t = malloc(n + 64); if (fread(t, 1, n, f) != (size_t)n) return 1; fclose(f);
    int v = atoi(argv[2]), rounds = argc > 3 ? atoi(argv[3]) : 5;
    for (int r = 0; r < rounds; r++) {
        struct timespec a, b; clock_gettime(CLOCK_MONOTONIC, &a);
        i64 ans = v == 0 ? v0(t, n) : v == 1 ? v1(t, n) : v == 2 ? v2(t, n) : v3(t, n);
        clock_gettime(CLOCK_MONOTONIC, &b);
        printf("%ld %.1f ms\n", (long)ans, (b.tv_sec - a.tv_sec) * 1e3 + (b.tv_nsec - a.tv_nsec) / 1e6);
    }
    return 0;
}
