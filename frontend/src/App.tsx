import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, BookOpen, GitBranch, ShieldCheck, Sparkles, Users } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from './api';
import type { User, Workspace } from './types';
import { Empty, ErrorNotice, Loading, Modal, useToast } from './ui';
import { WorkspaceApp } from './Workspace';

export function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark">
        <BookOpen size={21} strokeWidth={1.9} />
      </div>
      <span className="brand-title">
        TeamWiki<span style={{ color: '#80ac95' }}>.</span>
      </span>
      <span className="brand-plus">BETA</span>
    </div>
  );
}
function Auth({ unavailable }: { unavailable: unknown }) {
  const [register, setRegister] = useState(false);
  const client = useQueryClient();
  const invite = new URLSearchParams(useLocation().search).get('token');
  const auth = useMutation({
    mutationFn: (body: Record<string, string>) =>
      api<{ user: User }>(`/auth/${register ? 'register' : 'login'}`, { method: 'POST', body }),
    onSuccess: (data) => {
      client.setQueryData(['me'], data.user);
    },
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    auth.mutate(Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>);
  }
  return (
    <div className="auth-screen">
      <section className="auth-story">
        <Brand />
        <div className="auth-hero">
          <div className="auth-eyebrow">
            <span style={{ width: 5, height: 5, borderRadius: 10, background: '#89b697' }} />A
            shared place for better ideas
          </div>
          <h1>
            让团队知识，
            <br />
            <em>彼此连接。</em>
          </h1>
          <p>从一个想法，到一份共识。把值得留下的知识放在一起，让每个人都能找到答案。</p>
          <div className="auth-features">
            <span>
              <GitBranch size={14} />
              双向链接
            </span>
            <span>
              <Users size={14} />
              团队协作
            </span>
            <span>
              <ShieldCheck size={14} />
              权限保障
            </span>
          </div>
        </div>
        <div className="auth-caption">
          LESS SEARCHING. MORE KNOWING. &nbsp; © {new Date().getFullYear()} TeamWiki
        </div>
      </section>
      <section className="auth-form-wrap">
        <div className="auth-card">
          <div className="eyebrow">YOUR TEAM'S SECOND BRAIN</div>
          <h2>{register ? '开始积累，共同成长' : '欢迎回来'}</h2>
          <p>
            {invite
              ? '登录或创建账户后，即可接受团队邀请。'
              : register
                ? '创建你的账户，为团队搭建一个专属知识空间。'
                : '登录你的知识空间，接着上一次的灵感。'}
          </p>
          {!!unavailable && <ErrorNotice error={unavailable} />}
          <form onSubmit={submit}>
            {register && (
              <label className="field">
                <span>你的名字</span>
                <input
                  name="name"
                  required
                  maxLength={100}
                  autoComplete="name"
                  placeholder="团队成员如何称呼你"
                />
              </label>
            )}
            <label className="field">
              <span>电子邮箱</span>
              <input
                name="email"
                type="email"
                required
                autoComplete="email"
                placeholder="you@company.com"
              />
            </label>
            <label className="field">
              <span>密码</span>
              <input
                name="password"
                type="password"
                minLength={10}
                required
                autoComplete={register ? 'new-password' : 'current-password'}
                placeholder={register ? '至少 10 位字符' : '输入你的密码'}
              />
            </label>
            <ErrorNotice error={auth.error} />
            <button className="button primary" disabled={auth.isPending}>
              {auth.isPending ? '正在连接…' : register ? '创建账户' : '登录空间'}
              <ArrowRight size={14} />
            </button>
          </form>
          <div className="auth-switch">
            {register ? '已经有账户？' : '还没有账户？'}
            <button
              onClick={() => {
                setRegister(!register);
                auth.reset();
              }}
            >
              {register ? '立即登录' : '免费创建'}
            </button>
          </div>
          <div className="auth-footnote">
            属于团队的知识，安全地留在团队。
            <br />
            每一次记录，都让协作更进一步。
          </div>
        </div>
      </section>
    </div>
  );
}
export function NewWorkspace({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const create = useMutation({
    mutationFn: (name: string) => api<Workspace>('/workspaces', { method: 'POST', body: { name } }),
    onSuccess: (workspace) => {
      client.invalidateQueries({ queryKey: ['workspaces'] });
      navigate(`/w/${workspace.id}`);
      toast('知识空间已创建');
      onClose();
    },
  });
  return (
    <Modal title="创建知识空间" onClose={onClose}>
      <p className="modal-description">给团队一个共享知识的地方。你将成为这个空间的所有者。</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(new FormData(event.currentTarget).get('name') as string);
        }}
      >
        <label className="field">
          <span>空间名称</span>
          <input name="name" required maxLength={120} placeholder="例如：产品与研发团队" />
        </label>
        <ErrorNotice error={create.error} />
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={create.isPending}>
            {create.isPending ? '正在创建…' : '创建空间'}
            <ArrowRight size={13} />
          </button>
        </div>
      </form>
    </Modal>
  );
}
function Invitation({ token }: { token: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const accept = useMutation({
    mutationFn: () => api<Workspace>('/invitations/accept', { method: 'POST', body: { token } }),
    onSuccess: (workspace) => {
      client.invalidateQueries({ queryKey: ['workspaces'] });
      navigate(`/w/${workspace.id}`, { replace: true });
    },
  });
  return (
    <Empty
      title="加入团队知识空间"
      description="你收到了一份团队邀请。接受后，你可以访问空间中的文档。"
    >
      <ErrorNotice error={accept.error} />
      <button
        className="button primary"
        style={{ marginTop: 16 }}
        disabled={accept.isPending}
        onClick={() => accept.mutate()}
      >
        <Users size={14} />
        {accept.isPending ? '正在加入…' : '接受邀请'}
      </button>
      <button
        className="button"
        style={{ marginLeft: 8 }}
        onClick={() => navigate('/', { replace: true })}
      >
        稍后再说
      </button>
    </Empty>
  );
}
export default function App() {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api<User>('/auth/me'),
    staleTime: 60_000,
  });
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api<Workspace[]>('/workspaces'),
    enabled: !!me.data,
  });
  const [create, setCreate] = useState(false);
  const location = useLocation();
  const token = new URLSearchParams(location.search).get('token');
  if (me.isPending) return <Loading text="正在打开你的知识空间…" />;
  if (!me.data)
    return (
      <Auth
        unavailable={
          me.error && (!(me.error instanceof ApiError) || me.error.status !== 401) ? me.error : null
        }
      />
    );
  if (token)
    return (
      <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center' }}>
        <Invitation token={token} />
      </div>
    );
  if (workspaces.isPending) return <Loading />;
  if (workspaces.error)
    return (
      <Empty title="暂时无法加载空间" description="请检查连接后重试。">
        <ErrorNotice error={workspaces.error} />
        <button className="button" onClick={() => workspaces.refetch()}>
          重试
        </button>
      </Empty>
    );
  if (!workspaces.data?.length)
    return (
      <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center' }}>
        <Empty
          title={`你好，${me.data.name}`}
          description="从一个知识空间开始。记录想法，连接文档，和团队一起积累有价值的知识。"
        >
          <button className="button primary" onClick={() => setCreate(true)}>
            <Sparkles size={15} />
            创建第一个空间
          </button>
        </Empty>
        {create && <NewWorkspace onClose={() => setCreate(false)} />}
      </div>
    );
  return <WorkspaceApp user={me.data} workspaces={workspaces.data} />;
}
