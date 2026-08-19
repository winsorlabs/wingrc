// The 14 NIST 800-171 / CMMC L2 control families, in the standard
// (non-alphabetical) domain ordering already established by
// AssessmentBoard.tsx before this module existed — extracted here so a
// second widget (the dashboard's family radar chart) doesn't introduce a
// competing order for the same 14 families.
export const FAMILY_ORDER = [
  "AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PS", "PE", "RA", "CA", "SC", "SI",
] as const;
