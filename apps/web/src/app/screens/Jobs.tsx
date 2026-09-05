/**
 * Work in progress.
 *
 * Jobs run for minutes and outlive the tab, so there has to be somewhere to see what is still
 * running and what finished while you were away. Without this the app has no answer to "I
 * closed it — did my render survive?", which DEVELOPMENT.md makes a hard requirement.
 *
 * There is no percentage here, and that is deliberate. A stage either has not started, is
 * running, or is done; inventing a progress bar out of elapsed time would be a number the
 * system does not actually know, and a bar that stalls at 80% is worse than no bar.
 */
import { useEffect } from 'react'

import type { JobOut } from '@lumina/client'

import { useJobs, useRetryJob } from '../../api/queries'
import { IconCheck, IconClose, IconRefresh } from '../icons'

/** What each stage is called in the interface. The API's names are for the API. */
const STAGE_LABEL: Record<string, string> = {
  ingest: 'Reading your file',
  transcribe: 'Listening',
  analyze: 'Finding the moments',
  plan: 'Planning',
  generate: 'Picture',
  voice: 'Voice',
  captions: 'Words on screen',
  compose: 'Putting it together',
}

export function Jobs({ onClose, projectId }: { onClose: () => void; projectId: string | null }) {
  const jobs = useJobs(projectId)
  const retry = useRetryJob(projectId)

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const rows = jobs.data ?? []
  const running = rows.filter((j) => j.status === 'running' || j.status === 'queued')

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="sheet" role="dialog" aria-label="Work in progress">
        <div className="grab" />
        <div className="spread" style={{ marginBottom: 18 }}>
          <b style={{ font: '600 19px/1 var(--display)' }}>Work in progress</b>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <IconClose size={18} />
          </button>
        </div>

        <p className="muted" style={{ margin: '0 0 18px', font: '400 14px/1.55 var(--ui)' }}>
          {running.length
            ? 'You can close this tab. We will let you know when each one lands.'
            : 'Nothing is running right now.'}
        </p>

        <div>
          {rows.map((job) => (
            <Row key={job.id} job={job} onRetry={() => retry.mutate(job.id)} />
          ))}
          {jobs.isLoading ? <p className="muted">Looking…</p> : null}
        </div>
      </aside>
    </>
  )
}

function Row({ job, onRetry }: { job: JobOut; onRetry: () => void }) {
  const label = STAGE_LABEL[job.stage] ?? job.stage
  const done = job.status === 'succeeded'
  const failed = job.status === 'failed'

  return (
    <div className="job">
      <span className="grow">
        <b>{label}</b>
        <span>{failed ? (job.error ?? 'Failed') : done ? 'Ready' : 'Working'}</span>
        {/* Indeterminate while it runs: the system knows the stage has started and not that
            it is 62% through it. */}
        {job.status === 'running' || job.status === 'queued' ? (
          <span className="meter" style={{ display: 'block', marginTop: 8 }}>
            <i style={{ width: job.status === 'running' ? '55%' : '12%' }} />
          </span>
        ) : null}
      </span>
      {done ? (
        <span className="tag good">
          <IconCheck size={13} />
          Done
        </span>
      ) : null}
      {failed ? (
        <button className="btn sm" onClick={onRetry} title="Try this one again">
          <IconRefresh size={15} />
        </button>
      ) : null}
    </div>
  )
}
