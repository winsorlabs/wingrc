interface Props {
  status: string | null;
}

export function StatementChip({ status }: Props) {
  if (!status) {
    return <span className="stmt-chip">no statement</span>;
  }
  return (
    <span className={`stmt-chip ${status}`}>
      {status === "draft" && "Draft"}
      {status === "reviewed" && "Reviewed"}
      {status === "approved" && "Approved"}
    </span>
  );
}
