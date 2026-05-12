/**
 * INR number → Indian-system words (Lakh / Crore notation).
 *
 * Examples:
 *   inrToWords(1597185) → "Fifteen Lakh Ninety Seven Thousand One Hundred and Eighty Five Rupees"
 *   inrToWords(1) → "One Rupee"
 *   inrToWords(100) → "One Hundred Rupees"
 *   inrToWords(0) → "Zero Rupees"
 *
 * Used by Step 3 (Financial) for auto-populating
 * `financial.estimated_contract_value_words` and at FINANCIAL gate when
 * Department Head edits the ECV.
 */

const ONES: Record<number, string> = {
  0: "Zero", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
  6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
  11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen", 15: "Fifteen",
  16: "Sixteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen",
};
const TENS: Record<number, string> = {
  2: "Twenty", 3: "Thirty", 4: "Forty", 5: "Fifty",
  6: "Sixty", 7: "Seventy", 8: "Eighty", 9: "Ninety",
};

function twoDigit(n: number): string {
  if (n < 20) return ONES[n];
  const tens = Math.floor(n / 10);
  const ones = n % 10;
  return ones === 0 ? TENS[tens] : `${TENS[tens]} ${ONES[ones]}`;
}

function threeDigit(n: number): string {
  if (n === 0) return "";
  if (n < 100) return twoDigit(n);
  const hundreds = Math.floor(n / 100);
  const rest = n % 100;
  return rest === 0
    ? `${ONES[hundreds]} Hundred`
    : `${ONES[hundreds]} Hundred and ${twoDigit(rest)}`;
}

/**
 * Convert a non-negative integer to its Indian-system words form,
 * followed by "Rupees" (or "Rupee" for 1).
 */
export function inrToWords(amount: number): string {
  if (!Number.isFinite(amount) || amount < 0) return "";
  const n = Math.round(amount);
  if (n === 0) return "Zero Rupees";
  if (n === 1) return "One Rupee";

  // Indian system breakdown:
  // crore = n / 10^7, lakh = (n % 10^7) / 10^5,
  // thousand = (n % 10^5) / 10^3, hundred-rest = n % 1000
  const crore = Math.floor(n / 10_000_000);
  const lakh = Math.floor((n % 10_000_000) / 100_000);
  const thousand = Math.floor((n % 100_000) / 1000);
  const rest = n % 1000;

  const parts: string[] = [];
  if (crore > 0) parts.push(`${threeDigit(crore)} Crore`);
  if (lakh > 0) parts.push(`${twoDigit(lakh)} Lakh`);
  if (thousand > 0) parts.push(`${twoDigit(thousand)} Thousand`);
  if (rest > 0) parts.push(threeDigit(rest));

  // For idiomatic English, "and" before the last sub-100 component
  // is already inserted by threeDigit; we don't add extra "and"s
  // between thousand/lakh/crore tiers.
  return `${parts.join(" ")} Rupees`;
}

/**
 * Format an integer as Indian-style grouped INR (lakh/crore commas).
 * Example: 1597185 → "₹15,97,185"
 */
export function formatINR(amount: number): string {
  if (!Number.isFinite(amount)) return "₹0";
  const n = Math.round(amount);
  if (n < 0) return `-${formatINR(-n)}`;
  if (n < 1000) return `₹${n}`;

  // Last 3 digits stay grouped; everything before is grouped by 2.
  const rest = n % 1000;
  const restStr = String(rest).padStart(3, "0");
  let prefix = Math.floor(n / 1000);
  const groups: string[] = [];
  while (prefix >= 100) {
    groups.unshift(String(prefix % 100).padStart(2, "0"));
    prefix = Math.floor(prefix / 100);
  }
  if (prefix > 0) groups.unshift(String(prefix));
  return `₹${groups.join(",")},${restStr}`;
}
