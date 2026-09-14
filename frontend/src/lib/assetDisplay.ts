// Display name vs. natural key -- see backend/app/importers/liongard.py's
// module docstring ("Display name vs. natural key") for the full
// reasoning. natural_key is the reconcile identity (serial number,
// asset tag, or whatever else a source keyed on) and must never be
// confused with a human-friendly label -- this is exactly the bug this
// helper exists to fix (the UI used to render natural_key as if it were
// a name). `display_name` lives in `attributes` (a canonical key, not a
// scope_entity column -- same place as make_oem/model/asset_tag), set
// today only by the Liongard importer (Alias -> Hostname -> natural_key);
// workbook and manual entry don't set it, so this falls back to
// natural_key for them, rendering exactly as before this change.
export function assetDisplayName(
  attributes: Record<string, unknown>,
  naturalKey: string
): string {
  const displayName = attributes.display_name;
  if (typeof displayName === "string" && displayName.trim()) return displayName;
  return naturalKey;
}
