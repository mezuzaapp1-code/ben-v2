import PropTypes from 'prop-types'
import {
  deriveFileStage,
  fileStageLabel,
  formatUploadBytes,
  isTransientProcessingStage,
  processingPercent,
  visualFileStage,
} from '../lib/fileStatus.js'
import { getMessageTextDirection } from '../lib/markdownDirection.js'
import { useWorkspaceFileInventory } from '../hooks/useWorkspaceFileInventory.jsx'
import './FileLifecycleStatus.css'

export function FileLifecycleStatus({ file = null, upload = null, className = '' }) {
  const stage = deriveFileStage(file, { upload })
  const visual = visualFileStage(stage)
  const label = fileStageLabel(stage, file, upload)
  const bytes = stage === 'uploading' ? formatUploadBytes(upload) : ''
  const percent = processingPercent(file, upload)
  const showSpinner = (isTransientProcessingStage(stage) || stage === 'uploading') && percent == null
  return (
    <span className={`file-lifecycle file-lifecycle--${visual} ${className}`.trim()}>
      <span className="file-lifecycle__row">
        {showSpinner ? <span className="file-lifecycle__spinner" aria-hidden="true" /> : null}
        <span className="file-lifecycle__label">{label}</span>
      </span>
      {bytes ? <span className="file-lifecycle__bytes">{bytes}</span> : null}
      {percent != null ? (
        <span className="file-lifecycle__track" aria-hidden="true">
          <span className="file-lifecycle__bar" style={{ width: `${percent}%` }} />
        </span>
      ) : null}
    </span>
  )
}

FileLifecycleStatus.propTypes = {
  file: PropTypes.object,
  upload: PropTypes.object,
  className: PropTypes.string,
}

export function FileLifecycleBubble({ message }) {
  const { rows, uploads } = useWorkspaceFileInventory()
  const fileId = String(message?.file_id || '').trim()
  const localId = String(message?.local_upload_id || '').trim()
  const row = fileId ? rows.find((item) => String(item.id) === fileId) : null
  const upload =
    (localId && uploads.find((item) => item.localId === localId)) ||
    row?.upload ||
    null
  const file = row || {
    id: fileId || localId,
    display_name: message?.file_name || message?.content,
    status: message?.file_status,
    processing_stage: message?.processing_stage,
    job_status: message?.job_status,
    extraction_status: message?.extraction_status,
    index_status: message?.index_status,
    failure_message: message?.failure_message,
  }
  const name =
    row?.display_name ||
    row?.original_filename ||
    message?.file_name ||
    upload?.name ||
    'File'
  const live = Boolean(row || upload)
  const stage = deriveFileStage(file, { upload })
  return (
    <div className={`bubble ${message?.role || 'user'} file-lifecycle-bubble`}>
      <div className="bubble-text file-lifecycle-bubble__name" dir={getMessageTextDirection(name)}>
        {name}
      </div>
      {live || message?.kind === 'file_library' ? (
        <FileLifecycleStatus file={file} upload={upload} />
      ) : (
        <span className="file-lifecycle__label">Attached</span>
      )}
      {stage === 'failed' && (file.failure_message || upload?.error) ? (
        <p className="file-lifecycle-bubble__fail">{file.failure_message || upload.error}</p>
      ) : null}
    </div>
  )
}

FileLifecycleBubble.propTypes = {
  message: PropTypes.object.isRequired,
}
