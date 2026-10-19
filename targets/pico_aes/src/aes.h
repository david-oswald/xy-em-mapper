/*
 * Minimal, deliberately unoptimised AES-128 (encrypt only).
 *
 * Byte-oriented textbook implementation: S-box table lookups, explicit
 * ShiftRows, xtime()-based MixColumns. No T-tables, no masking, no
 * constant-time tricks - it is meant to leak.
 */
#ifndef PICO_AES_AES_H
#define PICO_AES_AES_H

#include <stdint.h>

#define AES_BLOCK_SIZE   16
#define AES_KEY_SIZE     16
#define AES_ROUNDS       10
#define AES_ROUND_KEYS   ((AES_ROUNDS + 1) * AES_BLOCK_SIZE)

typedef struct {
    uint8_t round_keys[AES_ROUND_KEYS];
} aes128_ctx_t;

/* Expand a 16-byte key into the 11 round keys. */
void aes128_init(aes128_ctx_t *ctx, const uint8_t key[AES_KEY_SIZE]);

/* Encrypt one 16-byte block (ECB). in and out may alias. */
void aes128_encrypt(const aes128_ctx_t *ctx,
                    const uint8_t in[AES_BLOCK_SIZE],
                    uint8_t out[AES_BLOCK_SIZE]);

#endif
