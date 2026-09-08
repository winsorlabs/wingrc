// The 14 NIST 800-171 / CMMC L2 control families, in the standard
// (non-alphabetical) domain ordering already established by
// AssessmentBoard.tsx before this module existed — extracted here so a
// second widget (the dashboard's family radar chart) doesn't introduce a
// competing order for the same 14 families.
export const FAMILY_ORDER = [
  "AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PS", "PE", "RA", "CA", "SC", "SI",
] as const;

// Extracted from FamilySection.tsx alongside FAMILY_ORDER above, for the
// same reason: RolesPanel.tsx (G.7) needs the same family display names and
// shouldn't carry a second, driftable copy.
export const FAMILY_NAMES: Record<string, string> = {
  AC: "Access Control",
  AT: "Awareness and Training",
  AU: "Audit and Accountability",
  CM: "Configuration Management",
  IA: "Identification and Authentication",
  IR: "Incident Response",
  MA: "Maintenance",
  MP: "Media Protection",
  PS: "Personnel Security",
  PE: "Physical Protection",
  RA: "Risk Assessment",
  CA: "Security Assessment",
  SC: "System and Communications Protection",
  SI: "System and Information Integrity",
};
