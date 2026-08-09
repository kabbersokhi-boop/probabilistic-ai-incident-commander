import { unavailable } from "./schema";
export const number = (v: unknown, digits = 2) =>
    typeof v === "number" && Number.isFinite(v)
        ? new Intl.NumberFormat("en-US", {
              maximumFractionDigits: digits,
          }).format(v)
        : unavailable;
export const integer = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v)
        ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(v)
        : unavailable;
export const percent = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v)
        ? `${(v * 100).toFixed(1)}%`
        : unavailable;
export const score = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : unavailable;
export const currency = (v: unknown, code: unknown) =>
    typeof v === "number" && typeof code === "string"
        ? new Intl.NumberFormat("en-US", {
              style: "currency",
              currency: code,
              maximumFractionDigits: 2,
          }).format(v)
        : unavailable;
export const time = (v: unknown) =>
    typeof v === "string" && !Number.isNaN(Date.parse(v))
        ? `${new Date(v).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" })} UTC`
        : unavailable;
