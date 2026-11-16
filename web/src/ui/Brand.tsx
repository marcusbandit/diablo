export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`brand ${compact ? "compact" : ""}`}>
      <img src={`${import.meta.env.BASE_URL}icon.svg`} alt="" width={compact ? 22 : 28} height={compact ? 22 : 28} />
      <span className="wordmark">Diablo</span>
    </span>
  );
}
