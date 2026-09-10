'use client';

import { useCallback, useEffect, useState } from 'react';
import { Ban, KeyRound, RotateCcw, Search, Users } from 'lucide-react';
import { API_BASE } from '@/config/env';

type AdminUser = { id: string; email: string; username?: string | null; display_name?: string | null; role: string; suspended_at?: string | null; suspension_reason?: string | null; created_at: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem('access_token') ?? sessionStorage.getItem('access_token');
  const response = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, ...(init?.body ? { 'Content-Type': 'application/json' } : {}) } });
  if (!response.ok) throw new Error(`admin_users_${response.status}`);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function UsersView({ onNotify }: { onNotify: (message: string, type?: 'success' | 'error' | 'info') => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try { setUsers(await request<AdminUser[]>(`/admin/users?query=${encodeURIComponent(query)}`)); }
    catch { onNotify('Không thể tải danh sách người dùng', 'error'); }
    finally { setLoading(false); }
  }, [onNotify, query]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void load());
    return () => window.cancelAnimationFrame(frame);
  }, [load]);

  const suspend = async (user: AdminUser) => {
    const reason = window.prompt('Lý do khóa tài khoản:', user.suspension_reason || 'Vi phạm điều khoản sử dụng');
    if (!reason) return;
    try { await request(`/admin/users/${user.id}/suspend`, { method: 'POST', body: JSON.stringify({ reason }) }); await load(); onNotify('Đã khóa tài khoản và thu hồi phiên đăng nhập'); }
    catch { onNotify('Không thể khóa tài khoản', 'error'); }
  };
  const unsuspend = async (user: AdminUser) => {
    try { await request(`/admin/users/${user.id}/unsuspend`, { method: 'POST' }); await load(); onNotify('Đã mở khóa tài khoản'); }
    catch { onNotify('Không thể mở khóa tài khoản', 'error'); }
  };
  const resetPassword = async (user: AdminUser) => {
    if (!window.confirm(`Gửi email đặt lại mật khẩu tới ${user.email}?`)) return;
    try { await request(`/admin/users/${user.id}/password-reset`, { method: 'POST' }); onNotify('Đã gửi email đặt lại mật khẩu'); }
    catch { onNotify('Không thể gửi email đặt lại mật khẩu', 'error'); }
  };

  return <section className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-bold">Quản lý người dùng</h2><p className="text-sm text-gray-500">Khóa tài khoản, mở khóa và gửi liên kết đặt lại mật khẩu.</p></div><div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400"/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Email, tên người dùng…" className="h-10 w-72 rounded-xl border bg-white pl-9 pr-3 text-sm dark:bg-[#1C1F27]" /></div></div>
    <div className="overflow-hidden rounded-2xl border bg-white dark:border-[#2A2E3D] dark:bg-[#1C1F27]">
      <table className="w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500 dark:bg-[#232630]"><tr><th className="px-4 py-3">Người dùng</th><th className="px-4 py-3">Vai trò</th><th className="px-4 py-3">Trạng thái</th><th className="px-4 py-3 text-right">Thao tác</th></tr></thead><tbody>
        {users.map((user) => <tr key={user.id} className="border-t dark:border-[#2A2E3D]"><td className="px-4 py-3"><div className="font-semibold">{user.display_name || user.username || user.email}</div><div className="text-xs text-gray-500">{user.email}</div></td><td className="px-4 py-3 capitalize">{user.role}</td><td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-xs font-semibold ${user.suspended_at ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'}`}>{user.suspended_at ? 'Đã khóa' : 'Hoạt động'}</span>{user.suspension_reason && <div className="mt-1 max-w-xs text-xs text-gray-500">{user.suspension_reason}</div>}</td><td className="px-4 py-3"><div className="flex justify-end gap-2"><button title="Gửi đặt lại mật khẩu" onClick={() => void resetPassword(user)} className="rounded-lg border p-2 hover:bg-gray-50"><KeyRound className="h-4 w-4"/></button>{user.suspended_at ? <button title="Mở khóa" onClick={() => void unsuspend(user)} className="rounded-lg border p-2 text-emerald-600 hover:bg-emerald-50"><RotateCcw className="h-4 w-4"/></button> : <button title="Khóa" onClick={() => void suspend(user)} disabled={user.role === 'admin'} className="rounded-lg border p-2 text-rose-600 hover:bg-rose-50 disabled:opacity-40"><Ban className="h-4 w-4"/></button>}</div></td></tr>)}
        {!loading && users.length === 0 && <tr><td colSpan={4} className="px-4 py-12 text-center text-gray-500"><Users className="mx-auto mb-2 h-8 w-8"/>Không tìm thấy người dùng</td></tr>}
      </tbody></table>{loading && <div className="p-6 text-center text-sm text-gray-500">Đang tải…</div>}
    </div>
  </section>;
}
