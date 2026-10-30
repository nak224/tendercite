import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getOpportunity,
  type OpportunityMetadata,
  type OpportunityRecord,
} from '../api/opportunities';
import { getPipelineEntry, type PipelineEntry } from '../api/pipeline';
import { uuid } from '../api/capabilityMappingValidation';
import { Async } from './ui';

export interface SavedNativeContext {
  opportunity?: OpportunityMetadata;
  entry?: PipelineEntry;
  opportunityRecord?: OpportunityRecord;
}
type Selection =
  { kind: 'NONE' } | { kind: 'INVALID' } | { kind: 'opportunity_id' | 'pipeline_id'; id: string };
export function nativeSelection(
  params: URLSearchParams,
  mode: 'matching' | 'pipeline' | 'opportunities',
): Selection {
  const keys = [...params.keys()];
  if (!keys.length) return { kind: 'NONE' };
  if (
    params.toString().length > 128 ||
    keys.length !== 1 ||
    !(keys[0] === 'opportunity_id' || (mode === 'pipeline' && keys[0] === 'pipeline_id'))
  )
    return { kind: 'INVALID' };
  const key = keys[0] as 'opportunity_id' | 'pipeline_id';
  const id = params.get(key);
  return uuid(id) ? { kind: key, id } : { kind: 'INVALID' };
}
export function SavedNativeSelection({
  mode,
  children,
}: {
  mode: 'matching' | 'pipeline' | 'opportunities';
  children: (context: SavedNativeContext) => ReactNode;
}) {
  const [params] = useSearchParams();
  const selection = nativeSelection(params, mode);
  if (selection.kind === 'NONE') return children({});
  if (selection.kind === 'INVALID')
    return (
      <section className="panel">
        <p role="alert">
          Invalid saved-record link. Choose records through the normal page; no supplied record data
          was trusted.
        </p>
        <Link to={`/${mode}`}>Choose records manually</Link>
      </section>
    );
  return (
    <ResolveSelection
      key={`${mode}:${selection.kind}:${selection.id}`}
      selection={selection}
      mode={mode}
    >
      {children}
    </ResolveSelection>
  );
}
function ResolveSelection({
  selection,
  mode,
  children,
}: {
  selection: { kind: 'opportunity_id' | 'pipeline_id'; id: string };
  mode: 'matching' | 'pipeline' | 'opportunities';
  children: (context: SavedNativeContext) => ReactNode;
}) {
  const query = useQuery({
    queryKey: ['tendercite', 'native-context', mode, selection.kind, selection.id],
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async ({ signal }): Promise<SavedNativeContext> => {
      if (selection.kind === 'pipeline_id') {
        const entry = await getPipelineEntry(selection.id, signal);
        return { entry, opportunity: entry.opportunity };
      }
      const r = await getOpportunity(selection.id, signal);
      return {
        ...(mode === 'opportunities' ? { opportunityRecord: r } : {}),
        opportunity: {
          opportunity_id: r.opportunity_id,
          revision: r.revision,
          title: r.opportunity.title,
          opportunity_type: r.opportunity.opportunity_type,
          organization: r.opportunity.organization,
          deadline: r.opportunity.deadline,
          updated_at: r.updated_at,
        },
      };
    },
  });
  return <Async query={query}>{(context) => children(context)}</Async>;
}
