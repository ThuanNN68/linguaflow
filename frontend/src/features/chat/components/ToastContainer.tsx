import React from 'react';
import { ToastItem } from '../types';
import { CheckCircle2, AlertCircle, Sparkles, Info, X } from 'lucide-react';

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onDismiss }) => {
  if (toasts.length === 0) return null;

  return (
    <div
      id="toast-notifications-container"
      className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none select-none"
    >
      {toasts.map((toast) => {
        let Icon = Info;
        let iconColor = 'text-blue-500';

        if (toast.type === 'success') {
          Icon = CheckCircle2;
          iconColor = 'text-emerald-500';
        } else if (toast.type === 'warning') {
          Icon = AlertCircle;
          iconColor = 'text-amber-500';
        } else if (toast.type === 'translation') {
          Icon = Sparkles;
          iconColor = 'text-[#2563EB]';
        }

        return (
          <div
            key={toast.id}
            className="pointer-events-auto flex items-start gap-3 p-3.5 bg-white dark:bg-[#1C1F27] border border-[#E8EAF0] dark:border-[#2A2E3D] rounded-2xl shadow-xl animate-in slide-in-from-bottom-5 duration-200"
          >
            <Icon className={`w-5 h-5 ${iconColor} flex-shrink-0 mt-0.5`} />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                {toast.title}
              </p>
              {toast.message && (
                <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] mt-0.5 truncate">
                  {toast.message}
                </p>
              )}
            </div>
            <button
              onClick={() => onDismiss(toast.id)}
              className="p-1 text-[#74798C] hover:text-[#1E2230] dark:hover:text-white"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
};
