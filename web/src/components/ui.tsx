import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';

// Original presentation helpers, without workforce/domain-schema dependencies.
export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
export function Async<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending)
    return (
      <p role="status" className="empty">
        Loading…
      </p>
    );
  if (query.isError)
    return (
      <div role="alert" className="notice danger">
        {query.error.message} <button onClick={() => void query.refetch()}>Retry</button>
      </div>
    );
  return <>{children(query.data)}</>;
}
