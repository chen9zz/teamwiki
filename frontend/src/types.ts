export type Role = 'owner' | 'admin' | 'editor' | 'viewer';
export interface User {
  id: string;
  email: string;
  name: string;
}
export interface Workspace {
  id: string;
  name: string;
  role: Role;
}
export interface Page {
  id: string;
  workspace_id: string;
  parent_id: string | null;
  title: string;
  content: string;
  tags: string[];
  version: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string;
}
export interface Revision {
  id?: string;
  page_id: string;
  version: number;
  title: string;
  content: string;
  tags: string[];
  parent_id: string | null;
  deleted_at?: string | null;
  is_deleted?: boolean;
  created_at: string;
  created_by?: string;
}
export interface PageLink {
  id: string;
  source_id: string;
  target_id: string | null;
  target_title: string | null;
  source_title?: string | null;
  raw_target: string;
  state: 'resolved' | 'unresolved' | 'ambiguous' | 'deleted';
  source_version: number;
}
export interface Links {
  outgoing: PageLink[];
  backlinks: PageLink[];
}
export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
}
export interface ImportJob {
  id: string;
  status: string;
  total: number;
  processed: number;
  error: string | null;
  result?: unknown;
}
export interface GraphData {
  nodes: { id: string; title: string }[];
  edges: { source: string; target: string }[];
}
export interface Conflict {
  code: 'version_conflict';
  current: Page;
  submitted_version: number;
}
export interface AuditEvent {
  id: string;
  action: string;
  created_at: string;
  actor_id?: string;
  user_id?: string;
  page_id?: string;
  entity_id?: string;
  details?: unknown;
  metadata?: unknown;
}

export type PageSummary = Pick<
  Page,
  'id' | 'workspace_id' | 'parent_id' | 'title' | 'tags' | 'version' | 'updated_at'
> & { has_children?: boolean };
export interface PageBatch {
  items: PageSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export type WikiLink = Pick<PageLink, 'raw_target' | 'target_id' | 'state'>;
export interface RevisionLinks {
  outgoing: WikiLink[];
  resolution: 'current_workspace';
}
