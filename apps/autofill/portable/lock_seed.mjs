// node portable/lock_seed.mjs OUT < answers.json   (passphrase in AUTOFILL_SEED_PASSPHRASE)
// Encrypts answers for a portable copy: PBKDF2-SHA256 (600,000 rounds) + AES-256-GCM, the same
// Web Crypto calls the extension uses to unlock them (extension/portable.js).
import { webcrypto as crypto } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { normalizePassphrase } from "../extension/portable.js";

const ITER = 600000;
const b64 = (u8) => Buffer.from(u8).toString("base64");
const plain = new TextEncoder().encode(readFileSync(0, "utf8"));
const salt = crypto.getRandomValues(new Uint8Array(16));
const iv = crypto.getRandomValues(new Uint8Array(12));
const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(normalizePassphrase(process.env.AUTOFILL_SEED_PASSPHRASE || "")), "PBKDF2", false, ["deriveKey"]);
const key = await crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations: ITER }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt"]);
const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain));
writeFileSync(process.argv[2], JSON.stringify({ v: 1, kdf: "PBKDF2-SHA256", iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) }) + "\n");
