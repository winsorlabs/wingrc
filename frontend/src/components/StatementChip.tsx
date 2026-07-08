interface Props {
  count: number;
  total: number;
  worstStatus: string | null;
}

export function StatementChip({ count, total, worstStatus }: Props) {
  if (count === 0) {
    return <span className="stmt-chip">no statements</span>;
  }
  const label = count === total ? `${total}/${total}` : `${count}/${total}`;
  return (
    <span className={`stmt-chip ${worstStatus ?? ""}`}>
      {label} &middot; {worstStatus}
    </span>
  );
}
