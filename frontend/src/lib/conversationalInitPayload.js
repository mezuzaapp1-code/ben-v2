import { normalizeProjectSlug } from './threadWorkspace.js'

/** @typedef {Object} ConversationalInitFormValues
 * @property {string} name
 * @property {string} software_description
 * @property {string} [location_base]
 * @property {string} [key_contacts]
 * @property {string} [initial_tactical_tasks]
 * @property {string} [description]
 */

/** @typedef {Object} ConversationalInitRequestBody
 * @property {string} name
 * @property {string} software_description
 * @property {string} status
 * @property {string} [description]
 * @property {string} [location_base]
 * @property {string} [key_contacts]
 * @property {string} [initial_tactical_tasks]
 */

/**
 * Build a request body aligned with backend ConversationalInitBody (Pydantic extra=forbid).
 * @param {ConversationalInitFormValues} form
 * @returns {ConversationalInitRequestBody}
 */
export function buildConversationalInitPayload(form) {
  const name = String(form?.name || '').trim()
  const softwareDescription = String(form?.software_description || '').trim()

  if (!name) {
    throw new Error('Project name is required.')
  }
  if (name.length > 512) {
    throw new Error('Project name must be 512 characters or fewer.')
  }
  if (!softwareDescription) {
    throw new Error('Software description is required for JIT schema provisioning.')
  }
  if (softwareDescription.length > 16000) {
    throw new Error('Software description must be 16000 characters or fewer.')
  }

  /** @type {ConversationalInitRequestBody} */
  const payload = {
    name,
    software_description: softwareDescription,
    status: 'active',
  }

  const description = String(form?.description || '').trim()
  if (description) {
    if (description.length > 8000) {
      throw new Error('Description must be 8000 characters or fewer.')
    }
    payload.description = description
  }

  const locationBase = String(form?.location_base || '').trim()
  if (locationBase) {
    if (locationBase.length > 256) {
      throw new Error('Location base must be 256 characters or fewer.')
    }
    payload.location_base = locationBase
  }

  const keyContacts = String(form?.key_contacts || '').trim()
  if (keyContacts) {
    if (keyContacts.length > 8000) {
      throw new Error('Key contacts must be 8000 characters or fewer.')
    }
    payload.key_contacts = keyContacts
  }

  const tacticalTasks = String(form?.initial_tactical_tasks || '').trim()
  if (tacticalTasks) {
    if (tacticalTasks.length > 8000) {
      throw new Error('Initial tactical tasks must be 8000 characters or fewer.')
    }
    payload.initial_tactical_tasks = tacticalTasks
  }

  return payload
}

/**
 * @param {Record<string, unknown>} response
 */
export function parseConversationalInitResponse(response) {
  const projectSlug = normalizeProjectSlug(response?.project_slug)
  if (!projectSlug) {
    throw new Error('Conversational init succeeded but project_slug was missing.')
  }

  const schemaBlueprint = Array.isArray(response?.schema_blueprint)
    ? response.schema_blueprint
    : []

  const projectId = String(response?.id || '').trim()
  if (!projectId) {
    throw new Error('Conversational init succeeded but project id was missing.')
  }

  return {
    projectId,
    projectSlug,
    projectName: String(response?.name || '').trim() || projectSlug,
    schemaBlueprint,
    tablesCreated: Number(response?.tables_created) || schemaBlueprint.length,
    softwareDescription: String(response?.software_description || '').trim(),
  }
}
