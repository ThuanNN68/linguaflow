"use client";

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { LanguageCode } from "../types";
import { tx } from "../i18n";
import type { ApiCalendarEvent, ApiConversation, ApiUser } from "../api/chat-api";
import {
  cancelCalendarEvent,
  createCalendarEvent,
  listConversations,
  listUsers,
  listCalendarEvents,
  updateCalendarEvent,
} from "../api/chat-api";
import {
  Bell, CalendarDays, Check, CheckCircle2, ChevronDown, ChevronLeft,
  ChevronRight, Clock, Clock3, FileText,
  MapPin, Pencil, Plus, RotateCw, Search, Trash2,
  Users, X,
} from "lucide-react";

/* ────────────────────────── TYPES & MODELS ────────────────────────── */
export type EventKind = "event" | "task";
export type TaskStatus = "pending" | "approved" | "rejected" | "done";
export type TaskSource = "manual" | "assistant";
export type CalendarMode = "day" | "week" | "month" | "schedule";
export type RecurrenceFreq = "none" | "daily" | "weekly" | "monthly" | "yearly" | "workdays";

export interface Attendee {
  email: string;
  name?: string;
  status?: "accepted" | "declined" | "tentative" | "needsAction";
}

export interface EventReminder {
  id: string;
  method: "popup" | "email";
  minutes: number;
}

type ReminderUnit = "minutes" | "hours" | "days";

export interface CalendarTask {
  id: string;
  title: string;
  kind: EventKind;

  // ── RÀNG BUỘC: MỖI LỊCH CHỈ TRONG 1 NGÀY ──
  date: string;               // "YYYY-MM-DD" e.g. "2026-08-27"
  isAllDay?: boolean;         // True nếu cả ngày
  startTime?: string;         // "HH:mm" e.g. "09:00"
  endTime?: string;           // "HH:mm" e.g. "10:30" (trong cùng ngày)
  duration: number;           // Số phút trong ngày

  // ISO timestamps cho grid & sorting
  dueAt: string;              // ISO start string
  endAt?: string;             // ISO end string

  // ── CÁC TRƯỜNG CƠ BẢN ──
  colorId?: string;           // Key in CALENDAR_PALETTE
  recurrence?: RecurrenceFreq;
  location?: string;          // Địa điểm / Phòng họp
  attendees?: Attendee[];     // Người tham gia
  reminders?: EventReminder[];// Thông báo
  note?: string;              // Ghi chú / Mô tả

  status: TaskStatus;
  source: TaskSource;
  category?: "meeting" | "task" | "personal" | "deadline";
  isRecurringInstance?: boolean;
  /** Dates in this recurring series that were completed individually. */
  completedOccurrenceDates?: string[];
}

/* ───────────────────────────── 11 EVENT COLORS ───────────────────────────── */
export interface CalendarColor {
  id: string;
  name: string;
  bg: string;
  text: string;
  light: string;
  border: string;
}

export const CALENDAR_PALETTE: Record<string, CalendarColor> = {
  peacock:   { id: "peacock",   name: "Lam khổng tước",  bg: "#039BE5", text: "#FFFFFF", light: "#E1F5FE", border: "#0288D1" },
  sage:      { id: "sage",      name: "Xô thơm",         bg: "#33B679", text: "#FFFFFF", light: "#E8F5E9", border: "#2E7D32" },
  grape:     { id: "grape",     name: "Nho tím",         bg: "#8E24AA", text: "#FFFFFF", light: "#F3E5F5", border: "#7B1FA2" },
  flamingo:  { id: "flamingo",  name: "Hồng hạc",        bg: "#E67C73", text: "#FFFFFF", light: "#FCE4EC", border: "#D81B60" },
  banana:    { id: "banana",    name: "Vàng chuối",      bg: "#F6BF26", text: "#202124", light: "#FFF9C4", border: "#FBC02D" },
  tangerine: { id: "tangerine", name: "Cam quýt",        bg: "#F4511E", text: "#FFFFFF", light: "#FBE9E7", border: "#E64A19" },
  tomato:    { id: "tomato",    name: "Cà chua đỏ",      bg: "#D50000", text: "#FFFFFF", light: "#FFEBEE", border: "#C62828" },
  basil:     { id: "basil",     name: "Húng quế",        bg: "#0B8043", text: "#FFFFFF", light: "#E8F5E9", border: "#1B5E20" },
  blueberry: { id: "blueberry", name: "Quả việt quất",   bg: "#3F51B5", text: "#FFFFFF", light: "#E8EAF6", border: "#303F9F" },
  lavender:  { id: "lavender",  name: "Oải hương",       bg: "#7986CB", text: "#FFFFFF", light: "#EDE7F6", border: "#5C6BC0" },
  graphite:  { id: "graphite",  name: "Graphit xám",     bg: "#616161", text: "#FFFFFF", light: "#EEEEEE", border: "#424242" },
};

export function getEventColor(task: CalendarTask): CalendarColor {
  if (task.status === "done") return CALENDAR_PALETTE.graphite;
  if (task.colorId && CALENDAR_PALETTE[task.colorId]) return CALENDAR_PALETTE[task.colorId];
  if (task.kind === "task") return CALENDAR_PALETTE.peacock;
  switch (task.category) {
    case "meeting":  return CALENDAR_PALETTE.sage;
    case "deadline": return CALENDAR_PALETTE.tomato;
    case "personal": return CALENDAR_PALETTE.flamingo;
    default:         return CALENDAR_PALETTE.peacock;
  }
}

/** Map one stored event onto the shape this component was written around.
 *
 *  The component predates the API and models a "task" with a duration and a
 *  status; the server models an event with a start and an end. Converting here
 *  keeps the whole rendering layer — day, week and month grids, the detail
 *  modal, the filters — working unchanged, which is most of the file.
 */
function toCalendarTask(event: ApiCalendarEvent): CalendarTask {
  const starts = new Date(event.starts_at);
  const ends = event.ends_at ? new Date(event.ends_at) : null;
  const minutes = ends ? Math.max(15, Math.round((+ends - +starts) / 60000)) : 60;
  const isAllDay = event.all_day;
  return {
    id: event.id,
    title: event.title,
    note: event.details ?? undefined,
    date: toDateString(starts),
    isAllDay,
    startTime: isAllDay ? undefined : toTimeString(starts),
    endTime: isAllDay ? undefined : toTimeString(ends ?? new Date(+starts + minutes * 60_000)),
    dueAt: event.starts_at,
    endAt: event.ends_at ?? undefined,
    duration: event.all_day ? 1440 : minutes,
    // Everything on the calendar has already been decided; a pending proposal
    // is in the task inbox, not here.
    status: event.status === "cancelled" ? "rejected" : "approved",
    source: event.source,
    category: event.all_day ? "personal" : "task",
    location: event.location ?? undefined,
    kind: event.all_day ? "event" : "task",
  };
}

const HOUR_HEIGHT = 64;
const DEFAULT_START_HOUR = 7;
const DEFAULT_END_HOUR = 22;

/* ────────────────────────── CUSTOM CHECKBOX (MÀU TRẮNG KHI CHƯA TÍCH) ────────────────────────── */
const CustomCheckbox: React.FC<{
  checked: boolean;
  onChange: (checked: boolean) => void;
  className?: string;
  ariaLabel?: string;
}> = ({ checked, onChange, className = "h-4 w-4", ariaLabel }) => {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onChange(!checked);
      }}
      className={`${className} shrink-0 rounded border flex items-center justify-center transition-all ${
        checked
          ? "border-[#1A73E8] bg-[#1A73E8] text-white shadow-xs"
          : "border-[#DADCE0] bg-white hover:border-[#1A73E8] dark:border-[#5F6368] dark:bg-white"
      }`}
    >
      {checked && <Check className="h-3 w-3 stroke-[3] text-white" />}
    </button>
  );
};

/* ────────────────────────── CONSTANTS & UTILITIES ────────────────────────── */
const MIN_DURATION = 15;

export function toDateString(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function toTimeString(d: Date): string {
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

export function parseDateString(str: string): Date {
  const [y, m, d] = str.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function buildSingleDayIso(dateStr: string, timeStr?: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const [hh, mm] = (timeStr || "09:00").split(":").map(Number);
  const date = new Date(y, m - 1, d, hh || 0, mm || 0, 0, 0);
  return date.toISOString();
}

export function timeToMinutes(timeStr?: string): number {
  if (!timeStr) return 0;
  const [h, m] = timeStr.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function minutesToTime(totalMins: number): string {
  const safe = Math.min(1439, Math.max(0, totalMins));
  const h = Math.floor(safe / 60);
  const m = safe % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/**
 * Events in this calendar are constrained to one day. A proposal may only
 * carry a start time, so never let the editor fall back to a fixed clock time
 * (for example 10:00) that is earlier than the proposed start.
 */
export function ensureEndTimeAfterStart(startTime: string, endTime?: string): string {
  const startMinutes = timeToMinutes(startTime);
  const endMinutes = timeToMinutes(endTime);
  if (endTime && endMinutes > startMinutes) return endTime;
  return minutesToTime(Math.min(1439, startMinutes + 60));
}

export function formatMinutesDuration(mins: number): string {
  if (mins >= 60) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m > 0 ? `${h} giờ ${m}p` : `${h} giờ`;
  }
  return `${mins} phút`;
}

export function formatVietnameseDate(date: Date, includeWeekday: boolean = true): string {
  const weekday = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"][date.getDay()];
  const day = date.getDate();
  const month = date.getMonth() + 1;
  const year = date.getFullYear();
  if (includeWeekday) {
    return `${weekday}, ${day} tháng ${month}, ${year}`;
  }
  return `${day} tháng ${month}, ${year}`;
}

export function sameDay(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

export function isAllDayTask(task: CalendarTask): boolean {
  return task.isAllDay === true;
}

function taskSpan(task: CalendarTask) {
  const startMins = task.startTime ? timeToMinutes(task.startTime) : 0;
  const dur = Math.max(MIN_DURATION, task.duration || (task.endTime ? timeToMinutes(task.endTime) - startMins : 60));
  return { start: startMins, end: Math.min(1440, startMins + dur) };
}

function hourRange() {
  return Array.from({ length: 24 }, (_, hour) => hour);
}

/* ────────────────────────── RECURRENCE MATCHING ────────────────────────── */
function matchesRecurrence(task: CalendarTask, targetDay: Date): boolean {
  const taskStart = parseDateString(task.date || toDateString(new Date(task.dueAt)));
  const startDay = new Date(taskStart.getFullYear(), taskStart.getMonth(), taskStart.getDate());
  const curDay = new Date(targetDay.getFullYear(), targetDay.getMonth(), targetDay.getDate());

  if (curDay < startDay) return false;
  if (sameDay(startDay, curDay)) return true;
  if (!task.recurrence || task.recurrence === "none") return false;

  if (task.recurrence === "daily") return true;
  if (task.recurrence === "workdays") {
    const dayOfWeek = curDay.getDay();
    return dayOfWeek >= 1 && dayOfWeek <= 5; // Thứ 2 đến Thứ 6
  }
  if (task.recurrence === "weekly") {
    return curDay.getDay() === startDay.getDay();
  }
  if (task.recurrence === "monthly") {
    return curDay.getDate() === startDay.getDate();
  }
  if (task.recurrence === "yearly") {
    return curDay.getDate() === startDay.getDate() && curDay.getMonth() === startDay.getMonth();
  }
  return false;
}

function occurrenceStatus(task: CalendarTask, occurrenceDate: string): TaskStatus {
  if (!task.recurrence || task.recurrence === "none") return task.status;
  return task.completedOccurrenceDates?.includes(occurrenceDate) ? "done" : "approved";
}

export function tasksOfDay(tasks: CalendarTask[], day: Date): CalendarTask[] {
  const targetDateStr = toDateString(day);
  const result: CalendarTask[] = [];

  for (const task of tasks) {
    const taskDateStr = task.date || toDateString(new Date(task.dueAt));
    if (taskDateStr === targetDateStr) {
      result.push({ ...task, status: occurrenceStatus(task, targetDateStr) });
    } else if (matchesRecurrence(task, day)) {
      const clonedDue = buildSingleDayIso(targetDateStr, task.startTime);
      const clonedEnd = task.endTime ? buildSingleDayIso(targetDateStr, task.endTime) : undefined;
      result.push({
        ...task,
        date: targetDateStr,
        dueAt: clonedDue,
        endAt: clonedEnd,
        isRecurringInstance: true,
        status: occurrenceStatus(task, targetDateStr),
      });
    }
  }
  return result;
}

/* ────────────────────────── OVERLAPPING EVENT LAYOUT ────────────────────────── */
interface PositionedTask {
  task: CalendarTask;
  top: number;
  height: number;
  left: number;
  width: number;
}

interface DetailPopoverAnchor {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/**
 * Thuật toán sắp xếp sự kiện trùng giờ:
 * - Nhóm các sự kiện giao thoa/trùng thời gian thành từng cụm (clusters)
 * - Xếp song song các cột con (sub-columns) cạnh nhau để không bị che khuất
 */
function layoutDay(tasks: CalendarTask[], startHour: number): PositionedTask[] {
  const timed = tasks
    .filter((task) => !isAllDayTask(task))
    .sort((a, b) => {
      const aSpan = taskSpan(a);
      const bSpan = taskSpan(b);
      if (aSpan.start !== bSpan.start) return aSpan.start - bSpan.start;
      return (bSpan.end - bSpan.start) - (aSpan.end - aSpan.start);
    });

  if (timed.length === 0) return [];

  // Gom các sự kiện trùng / giao thoa thành các cụm
  const clusters: CalendarTask[][] = [];
  let currentCluster: CalendarTask[] = [];
  let clusterEnd = -1;

  for (const task of timed) {
    const { start, end } = taskSpan(task);
    if (currentCluster.length === 0) {
      currentCluster.push(task);
      clusterEnd = end;
    } else if (start < clusterEnd) {
      // Có trùng / chạm khung giờ với cụm hiện tại
      currentCluster.push(task);
      clusterEnd = Math.max(clusterEnd, end);
    } else {
      clusters.push(currentCluster);
      currentCluster = [task];
      clusterEnd = end;
    }
  }
  if (currentCluster.length > 0) {
    clusters.push(currentCluster);
  }

  const positioned: PositionedTask[] = [];
  const offset = startHour * 60;

  for (const cluster of clusters) {
    // Phân bổ cột con bằng Greedy Interval Coloring
    const columns: CalendarTask[][] = [];
    const taskColMap = new Map<string, number>();

    for (const task of cluster) {
      const { start } = taskSpan(task);
      let colIndex = -1;
      for (let i = 0; i < columns.length; i++) {
        const lastInCol = columns[i][columns[i].length - 1];
        if (taskSpan(lastInCol).end <= start) {
          colIndex = i;
          columns[i].push(task);
          break;
        }
      }
      if (colIndex === -1) {
        colIndex = columns.length;
        columns.push([task]);
      }
      taskColMap.set(task.id, colIndex);
    }

    const numCols = Math.max(1, columns.length);
    const width = 100 / numCols;

    for (const task of cluster) {
      const { start, end } = taskSpan(task);
      const col = taskColMap.get(task.id) || 0;
      positioned.push({
        task,
        top: ((start - offset) / 60) * HOUR_HEIGHT,
        height: Math.max(26, ((end - start) / 60) * HOUR_HEIGHT - 2),
        left: col * width,
        width: width,
      });
    }
  }

  return positioned;
}

function startOfWeek(value: Date) {
  const date = new Date(value);
  const offset = (date.getDay() + 6) % 7; // Monday = 0
  date.setDate(date.getDate() - offset);
  date.setHours(0, 0, 0, 0);
  return date;
}

/* ────────────────────────── DEFAULT SAMPLE TASKS (WITH OVERLAPPING EXAMPLES) ────────────────────────── */
const defaultTasks = (): CalendarTask[] => {
  const today = new Date();
  const dStr = (offset: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() + offset);
    return toDateString(d);
  };

  const t0 = dStr(0);
  const t1 = dStr(1);
  const t2 = dStr(2);

  return [
    {
      id: "meeting-standup",
      title: "Họp Stand-up dự án đầu ngày",
      kind: "event",
      date: t0,
      isAllDay: false,
      startTime: "09:00",
      endTime: "09:30",
      duration: 30,
      dueAt: buildSingleDayIso(t0, "09:00"),
      endAt: buildSingleDayIso(t0, "09:30"),
      recurrence: "workdays", // Thứ 2 đến Thứ 6
      colorId: "sage",
      category: "meeting",
      location: "Phòng họp dự án",
      note: "Cập nhật nhanh tiến độ các đầu việc trong ngày với cả nhóm.",
      attendees: [
        { email: "leader@company.com", name: "Trưởng nhóm", status: "accepted" },
        { email: "dev@company.com", name: "Lập trình viên", status: "accepted" },
      ],
      reminders: [{ id: "r1", method: "popup", minutes: 10 }],
      status: "approved",
      source: "manual",
    },
    {
      id: "task-urgent-review",
      title: "Kiểm tra khẩn cấp báo cáo sự cố",
      kind: "task",
      date: t0,
      isAllDay: false,
      startTime: "09:00",
      endTime: "10:00", // Trùng khung giờ 9:00 - 9:30 với họp Stand-up để hiển thị song song!
      duration: 60,
      dueAt: buildSingleDayIso(t0, "09:00"),
      endAt: buildSingleDayIso(t0, "10:00"),
      colorId: "tomato",
      category: "deadline",
      note: "Công việc quan trọng diễn ra song song cùng khung giờ họp.",
      reminders: [{ id: "r2", method: "popup", minutes: 10 }],
      status: "approved",
      source: "manual",
    },
    {
      id: "task-code-review",
      title: "Rà soát mã nguồn & Tối ưu giao diện",
      kind: "task",
      date: t0,
      isAllDay: false,
      startTime: "09:45",
      endTime: "11:15", // Trùng khung giờ 9:45 - 10:00 với task kiểm tra khẩn cấp
      duration: 90,
      dueAt: buildSingleDayIso(t0, "09:45"),
      endAt: buildSingleDayIso(t0, "11:15"),
      colorId: "peacock",
      category: "task",
      note: "Tối ưu hóa khả năng kéo thả và hiển thị lịch trùng khung giờ.",
      reminders: [{ id: "r3", method: "popup", minutes: 15 }],
      status: "approved",
      source: "manual",
    },
    {
      id: "event-lunch",
      title: "Ăn trưa cùng nhóm thiết kế",
      kind: "event",
      date: t0,
      isAllDay: false,
      startTime: "12:00",
      endTime: "13:00",
      duration: 60,
      dueAt: buildSingleDayIso(t0, "12:00"),
      endAt: buildSingleDayIso(t0, "13:00"),
      colorId: "flamingo",
      category: "personal",
      location: "Khu ẩm thực Tầng B1",
      status: "approved",
      source: "manual",
    },
    {
      id: "task-send-report",
      title: "Gửi báo cáo tổng kết tuần cho khách hàng",
      kind: "task",
      date: t1,
      isAllDay: false,
      startTime: "15:00",
      endTime: "16:00",
      duration: 60,
      dueAt: buildSingleDayIso(t1, "15:00"),
      endAt: buildSingleDayIso(t1, "16:00"),
      colorId: "tomato",
      category: "deadline",
      note: "Tổng hợp số liệu và đính kèm file PDF.",
      reminders: [{ id: "r4", method: "popup", minutes: 30 }],
      status: "approved",
      source: "manual",
    },
    {
      id: "event-workshop",
      title: "Hội thảo chuyên đề công nghệ mới",
      kind: "event",
      date: t2,
      isAllDay: true,
      duration: 1440,
      dueAt: buildSingleDayIso(t2, "00:00"),
      endAt: buildSingleDayIso(t2, "23:59"),
      colorId: "grape",
      category: "meeting",
      location: "Trung tâm Hội nghị Quốc tế",
      status: "approved",
      source: "manual",
    },
  ];
};

function recurrenceLabel(rec?: RecurrenceFreq) {
  if (!rec || rec === "none") return "Không lặp lại";
  if (rec === "daily") return "Hằng ngày";
  if (rec === "workdays") return "Thứ 2 đến Thứ 6"; // Đã bỏ "Ngày làm việc"
  if (rec === "weekly") return "Hằng tuần vào ngày này";
  if (rec === "monthly") return "Hằng tháng vào ngày này";
  if (rec === "yearly") return "Hằng năm vào ngày này";
  return "Không lặp lại";
}

/* ────────────────────────── DRAG TIME SELECTION STATE ────────────────────────── */
interface DragTimeSelection {
  isDragging: boolean;
  dateStr: string;
  startMin: number;
  currentMin: number;
  columnTop: number;
  columnHeight: number;
}

interface PersonalCalendarProps {
  token: string;
  language: LanguageCode;
  onNotify?: (title: string, detail?: string, tone?: "success" | "warning") => void;
  /** Bumped by the parent when a socket event says the calendar moved. */
  refreshToken?: number;
}

export const PersonalCalendar: React.FC<PersonalCalendarProps> = ({
  token,
  language,
  onNotify,
  refreshToken = 0,
}) => {
  const [tasks, setTasks] = useState<CalendarTask[]>([]);
  const [mode, setMode] = useState<CalendarMode>("week");
  const [cursor, setCursor] = useState(() => new Date());
  const [query, setQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [newItemKind, setNewItemKind] = useState<EventKind>("event");
  const [draftDate, setDraftDate] = useState<string>(toDateString(new Date()));
  const [draftStartTime, setDraftStartTime] = useState<string>("09:00");
  const [draftEndTime, setDraftEndTime] = useState<string>("10:00");
  const [draftTitle, setDraftTitle] = useState<string>("");
  const [draftLocation, setDraftLocation] = useState<string>("");
  const [draftColorId, setDraftColorId] = useState<string>("sage");
  const [draftIsAllDay, setDraftIsAllDay] = useState<boolean>(false);

  const [selectedTask, setSelectedTask] = useState<CalendarTask | null>(null);
  const [detailAnchor, setDetailAnchor] = useState<DetailPopoverAnchor | null>(null);
  const [editingTask, setEditingTask] = useState<CalendarTask | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [selectedFilters, setSelectedFilters] = useState<Record<string, boolean>>({
    event: true,
    task: true,
  });

  // Kéo chọn khung giờ trực tiếp trên lịch
  const [dragSelection, setDragSelection] = useState<DragTimeSelection | null>(null);

  const createMenuRef = useRef<HTMLDivElement | null>(null);
  const hydrated = useRef(false);

  // Update current time tick
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  // Close menus on outside click
  useEffect(() => {
    if (!showCreateMenu) return;
    const handleOutside = (event: MouseEvent) => {
      if (!createMenuRef.current?.contains(event.target as Node)) setShowCreateMenu(false);
    };
    window.addEventListener("mousedown", handleOutside);
    return () => window.removeEventListener("mousedown", handleOutside);
  }, [showCreateMenu]);

  // The calendar lives on the server, not in this browser. It used to be held
  // in localStorage, which meant it existed only on one machine, could not be
  // reminded about, and could never be shared across devices.
  const reload = useCallback(async () => {
    try {
      const events = await listCalendarEvents(token);
      setTasks(events.map(toCalendarTask));
      hydrated.current = true;
    } catch (error) {
      onNotify?.(
        "Không tải được lịch",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    }
  }, [token, onNotify]);

  useEffect(() => {
    void reload();
  }, [reload, refreshToken]);

  // Global mousemove & mouseup handler for drag-to-select time range
  useEffect(() => {
    if (!dragSelection?.isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const y = e.clientY - dragSelection.columnTop;
      const rawMin = (y / dragSelection.columnHeight) * 1440;
      // Snap to 15-minute intervals
      const snappedMin = Math.max(0, Math.min(1440, Math.round(rawMin / 15) * 15));
      setDragSelection((prev) => (prev ? { ...prev, currentMin: snappedMin } : null));
    };

    const handleMouseUp = (e: MouseEvent) => {
      if (!dragSelection) return;

      const start = Math.min(dragSelection.startMin, dragSelection.currentMin);
      let end = Math.max(dragSelection.startMin, dragSelection.currentMin);

      // Nếu người dùng chỉ click hoặc kéo < 15 phút, tự động tạo khung 1 giờ
      if (end - start < 15) {
        end = Math.min(1440, start + 60);
      }

      const startTime = minutesToTime(start);
      const endTime = minutesToTime(end);

      setDraftDate(dragSelection.dateStr);
      setDraftStartTime(startTime);
      setDraftEndTime(endTime);
      setNewItemKind("event");
      setDraftTitle("");
      setDraftLocation("");
      setDraftColorId("sage");
      setDraftIsAllDay(false);
      setShowForm(true);
      setDragSelection(null);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [dragSelection]);

  // Filter visible tasks
  const visibleTasks = useMemo(() => {
    return tasks.filter((task) => {
      const q = query.trim().toLowerCase();
      const matchesSearch =
        !q ||
        `${task.title} ${task.note ?? ""} ${task.location ?? ""} ${task.attendees?.map((a) => a.email).join(" ") ?? ""}`
          .toLowerCase()
          .includes(q);

      const isEnabled = selectedFilters[task.kind] !== false;

      return matchesSearch && isEnabled;
    });
  }, [query, selectedFilters, tasks]);

  const scheduledTasks = useMemo(
    () => visibleTasks.filter((task) => task.status === "approved" || task.status === "done"),
    [visibleTasks]
  );

  const weekDays = useMemo(() => {
    const weekStart = startOfWeek(cursor);
    return Array.from({ length: 7 }, (_, index) => {
      const date = new Date(weekStart);
      date.setDate(weekStart.getDate() + index);
      return date;
    });
  }, [cursor]);

  const weekStart = weekDays[0];
  const hours = useMemo(() => hourRange(), []);

  const updateTask = (id: string, update: Partial<CalendarTask>) =>
    setTasks((items) => items.map((task) => (task.id === id ? { ...task, ...update } : task)));

  // Approval lives in the task inbox, where the proposal and its clarification
  // question are. The calendar shows what was already decided, so these three
  // only exist for entries that reached it.
  const approve = (task: CalendarTask) => updateTask(task.id, { status: "approved" });
  const reject = (task: CalendarTask) => updateTask(task.id, { status: "rejected" });
  const finish = (task: CalendarTask) => {
    if (!task.recurrence || task.recurrence === "none") {
      updateTask(task.id, { status: task.status === "done" ? "approved" : "done" });
      return;
    }

    // A recurring item is rendered as a dated occurrence. Keep completion on
    // that date only; changing the series status would complete every repeat.
    const occurrenceDate = task.date || toDateString(new Date(task.dueAt));
    setTasks((items) => items.map((item) => {
      if (item.id !== task.id) return item;
      const completedDates = new Set(item.completedOccurrenceDates ?? []);
      if (completedDates.has(occurrenceDate)) completedDates.delete(occurrenceDate);
      else completedDates.add(occurrenceDate);
      return { ...item, completedOccurrenceDates: [...completedDates] };
    }));
  };

  const removeTask = async (task: CalendarTask) => {
    // Optimistic, then reconciled by `reload`. The server cancels rather than
    // deletes, so a failure leaves the entry visible rather than losing it.
    setTasks((items) => items.filter((item) => item.id !== task.id));
    try {
      await cancelCalendarEvent(token, task.id);
    } catch (error) {
      onNotify?.(
        "Không xoá được",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    } finally {
      await reload();
    }
  };

  const saveTask = async (task: CalendarTask) => {
    try {
      if (tasks.some((item) => item.id === task.id)) {
        await updateCalendarEvent(token, task.id, {
          title: task.title,
          details: task.note ?? null,
          location: task.location ?? null,
          starts_at: new Date(task.dueAt).toISOString(),
          all_day: task.kind === "event",
        });
      } else {
        await createCalendarEvent(token, {
          title: task.title,
          starts_at: new Date(task.dueAt).toISOString(),
          details: task.note ?? null,
          location: task.location ?? null,
          all_day: task.kind === "event",
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        });
      }
    } catch (error) {
      onNotify?.(
        "Không lưu được",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    } finally {
      await reload();
    }
  };

  const deleteTask = (id: string) => {
    const task = tasks.find((item) => item.id === id);
    if (task) void removeTask(task);
    setSelectedTask(null);
    setDetailAnchor(null);
  };

  // Open Full Form with single-day parameters and prefilled drag details
  const openCreateForm = useCallback((
    dateStr: string,
    startTime: string = "09:00",
    endTime: string = "10:00",
    kind: EventKind = "event",
    title: string = "",
    colorId: string = "sage",
    location: string = "",
    isAllDay: boolean = false
  ) => {
    setDraftDate(dateStr);
    setDraftStartTime(startTime);
    setDraftEndTime(endTime);
    setNewItemKind(kind);
    setDraftTitle(title);
    setDraftColorId(colorId);
    setDraftLocation(location);
    setDraftIsAllDay(isAllDay);
    setShowForm(true);
    setDragSelection(null);
  }, []);

  // Xử lý khi bắt đầu kéo chuột trên cột ngày để chọn khung giờ linh hoạt
  const handleStartDragTime = useCallback((dateStr: string, e: React.MouseEvent<HTMLDivElement>) => {
    // Không kích hoạt kéo nếu click trúng sự kiện đã có
    if ((e.target as HTMLElement).closest("button[data-task-block]")) return;

    const columnEl = e.currentTarget;
    const rect = columnEl.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const totalHeight = 24 * HOUR_HEIGHT;
    const rawMin = (y / totalHeight) * 1440;
    const snappedStart = Math.max(0, Math.min(1425, Math.floor(rawMin / 15) * 15));

    setDragSelection({
      isDragging: true,
      dateStr,
      startMin: snappedStart,
      currentMin: snappedStart + 30,
      columnTop: rect.top,
      columnHeight: totalHeight,
    });
  }, []);

  const handleResizeDragTime = useCallback((dateStr: string, fixedMinute: number, e: React.MouseEvent<HTMLElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const column = e.currentTarget.closest("[data-time-column]") as HTMLDivElement | null;
    if (!column) return;
    const rect = column.getBoundingClientRect();
    const rawMinute = ((e.clientY - rect.top) / (24 * HOUR_HEIGHT)) * 1440;
    const snappedMinute = Math.max(0, Math.min(1440, Math.round(rawMinute / 15) * 15));
    setDragSelection({
      isDragging: true,
      dateStr,
      startMin: fixedMinute,
      currentMin: snappedMinute,
      columnTop: rect.top,
      columnHeight: 24 * HOUR_HEIGHT,
    });
  }, []);

  const openTaskDetails = useCallback((task: CalendarTask, anchor?: DetailPopoverAnchor) => {
    setDetailAnchor(anchor ?? null);
    setSelectedTask(task);
  }, []);

  const navigate = useCallback(
    (direction: -1 | 1) => {
      setCursor((date) => {
        const next = new Date(date);
        if (mode === "day") next.setDate(next.getDate() + direction);
        else if (mode === "week") next.setDate(next.getDate() + direction * 7);
        else if (mode === "month") next.setMonth(next.getMonth() + direction);
        else next.setDate(next.getDate() + direction * 14);
        return next;
      });
    },
    [mode]
  );

  const currentPeriod = useMemo(() => {
    if (mode === "day") {
      return cursor.toLocaleDateString(language, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    }
    if (mode === "week") {
      return `${weekStart.toLocaleDateString(language, { day: "numeric", month: "short" })} – ${weekDays[6].toLocaleDateString(language, { day: "numeric", month: "short", year: "numeric" })}`;
    }
    if (mode === "month") {
      return cursor.toLocaleDateString(language, { month: "long", year: "numeric" });
    }
    return `${tx(language, "Schedule")} (${cursor.toLocaleDateString(language, { month: "long", year: "numeric" })})`;
  }, [mode, cursor, weekStart, weekDays, language]);

  return (
    <section className="flex h-screen min-w-0 flex-1 bg-[#F8FAFD] text-[#1F1F1F] select-none dark:bg-[#131314] dark:text-[#E3E3E3]" aria-label="Lịch cá nhân">
      {/* ── MAIN CALENDAR VIEWPORT ── */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* ── CALENDAR HEADER TOOLBAR ── */}
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#DADCE0] bg-white px-3 dark:border-[#36373A] dark:bg-[#1E1F20] sm:px-5">
          {/* Left: Calendar identity, Today, Navigation */}
          <div className="flex items-center gap-1.5 sm:gap-3">
            {/* Calendar logo */}
            <div className="mr-2 flex items-center gap-2.5">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#E8F0FE] text-[#1A73E8] shadow-sm dark:bg-[#1A73E8]/20 dark:text-[#A8C7FA]">
                <CalendarDays className="h-6 w-6" strokeWidth={2.25} />
              </div>
              <span className="hidden text-[22px] font-normal tracking-tight text-[#3C4043] dark:text-[#E3E3E3] md:inline">
                {tx(language, "Calendar")}
              </span>
            </div>

            {/* Today Button */}
            <button
              onClick={() => setCursor(new Date())}
              className="rounded-full border border-[#747775]/50 px-4 py-1.5 text-sm font-medium text-[#1F1F1F] transition-all hover:bg-[#F1F3F4] active:bg-[#E8EAED] dark:border-[#5F6368] dark:text-[#E3E3E3] dark:hover:bg-[#282A2C]"
            >
              {tx(language, "Today")}
            </button>

            {/* Chevron Prev / Next */}
            <div className="flex items-center">
              <button
                aria-label="Kỳ trước"
                onClick={() => navigate(-1)}
                className="flex h-9 w-9 items-center justify-center rounded-full text-[#444746] transition-colors hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#282A2C]"
              >
                <ChevronLeft className="h-5 w-5" />
              </button>
              <button
                aria-label="Kỳ sau"
                onClick={() => navigate(1)}
                className="flex h-9 w-9 items-center justify-center rounded-full text-[#444746] transition-colors hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#282A2C]"
              >
                <ChevronRight className="h-5 w-5" />
              </button>
            </div>

            {/* Period Display */}
            <h2 className="min-w-0 truncate text-sm font-normal text-[#1F1F1F] dark:text-[#E3E3E3] sm:text-lg lg:text-[20px]">
              {currentPeriod}
            </h2>
          </div>

          {/* Right: Search & View Mode Switcher */}
          <div className="flex items-center gap-2">
            {showSearch ? (
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#747775]" />
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onBlur={() => { if (!query) setShowSearch(false); }}
                  placeholder="Tìm kiếm sự kiện, việc cần làm..."
                  className="h-10 w-52 rounded-full border border-[#DADCE0] bg-[#F1F3F4] pl-9 pr-8 text-xs text-[#1F1F1F] outline-none transition-all focus:w-64 focus:border-[#1A73E8] focus:bg-white dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-white dark:focus:bg-[#1E1F20]"
                />
                <button
                  type="button"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => { setQuery(""); setShowSearch(false); }}
                  aria-label="Đóng tìm kiếm"
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1 text-[#747775] hover:bg-[#E0E0E0] dark:hover:bg-[#444746]"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowSearch(true)}
                aria-label="Tìm kiếm lịch"
                title="Tìm kiếm"
                className="flex h-9 w-9 items-center justify-center rounded-full text-[#444746] transition-colors hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#282A2C]"
              >
                <Search className="h-5 w-5" />
              </button>
            )}

            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as CalendarMode)}
              aria-label="Chế độ xem lịch"
              className="h-9 rounded-lg border border-[#747775]/40 bg-white px-3 text-xs font-medium text-[#444746] outline-none transition-colors hover:bg-[#F1F3F4] focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#C4C7C5] dark:hover:bg-[#36373A]"
            >
              <option value="day">{tx(language, "Day")}</option>
              <option value="week">{tx(language, "Week")}</option>
              <option value="month">{tx(language, "Month")}</option>
              <option value="schedule">{tx(language, "Schedule")}</option>
            </select>

          </div>
        </header>

        {/* ── CALENDAR BODY WORKSPACE ── */}
        <div className="flex min-h-0 flex-1 overflow-hidden">
          {/* ── LEFT SIDEBAR ── */}
          <aside className="hidden w-[260px] shrink-0 flex-col border-r border-[#DADCE0] bg-white dark:border-[#36373A] dark:bg-[#1E1F20] lg:flex">
            {/* Nút "+ Tạo" */}
            <div className="p-4" ref={createMenuRef}>
              <div className="relative">
                <button
                  onClick={() => setShowCreateMenu((v) => !v)}
                  className="flex h-12 w-fit items-center gap-3 rounded-full bg-white px-5 text-sm font-medium text-[#3C4043] shadow-[0_1px_3px_0_rgba(60,64,67,0.3),0_4px_8px_3px_rgba(60,64,67,0.15)] transition-all hover:bg-[#F8FAFD] hover:shadow-[0_2px_6px_2px_rgba(60,64,67,0.25)] active:scale-98 dark:bg-[#2D2E30] dark:text-[#E3E3E3] dark:hover:bg-[#36373A]"
                >
                  <Plus className="h-6 w-6 text-[#1A73E8] stroke-[2.5]" />
                  <span className="font-medium text-[15px]">{tx(language, "Create")}</span>
                  <ChevronDown className="h-4 w-4 text-[#747775]" />
                </button>

                {showCreateMenu && (
                  <div className="absolute left-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-2xl border border-[#DADCE0] bg-white py-1.5 shadow-[0_8px_24px_rgba(0,0,0,.16)] dark:border-[#5F6368] dark:bg-[#2D2E30]">
                    <button
                      onClick={() => {
                        setShowCreateMenu(false);
                        openCreateForm(toDateString(cursor), "09:00", "10:00", "event");
                      }}
                      className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-[#1F1F1F] transition-colors hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#36373A]"
                    >
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#E8F0FE] text-[#1A73E8] dark:bg-[#1A73E8]/20 dark:text-[#A8C7FA]">
                        <CalendarDays className="h-4 w-4" />
                      </span>
                      <span className="font-medium">{tx(language, "Event")}</span>
                    </button>

                    <button
                      onClick={() => {
                        setShowCreateMenu(false);
                        openCreateForm(toDateString(cursor), "10:00", "11:00", "task");
                      }}
                      className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-[#1F1F1F] transition-colors hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#36373A]"
                    >
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#E8F5E9] text-[#2E7D32] dark:bg-emerald-500/20 dark:text-emerald-300">
                        <CheckCircle2 className="h-4 w-4" />
                      </span>
                      <span className="font-medium">{tx(language, "Task")}</span>
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* Mini Month Calendar */}
            <div className="px-3 pb-2">
              <MiniCalendar
                cursor={cursor}
                onSelect={(d) => {
                  setCursor(d);
                  setMode("day");
                }}
                now={now}
                tasks={scheduledTasks}
              />
            </div>

            {/* "Lịch của tôi" Filter (Ô tích chọn màu trắng khi chưa tích) */}
            <div className="flex-1 overflow-y-auto px-4 py-2 text-xs">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-semibold text-[#444746] dark:text-[#C4C7C5] tracking-wide uppercase text-[11px]">
                  {tx(language, "My calendars")}
                </span>
              </div>

              <div className="space-y-1.5">
                {[
                  { key: "event", name: tx(language, "Event"), color: CALENDAR_PALETTE.sage },
                  { key: "task", name: tx(language, "Task"), color: CALENDAR_PALETTE.peacock },
                ].map((item) => {
                  const isChecked = selectedFilters[item.key] !== false;
                  return (
                    <label
                      key={item.key}
                      className="flex cursor-pointer items-center gap-2.5 rounded-lg py-1 px-1.5 hover:bg-[#F1F3F4] dark:hover:bg-[#282A2C]"
                    >
                      <CustomCheckbox
                        checked={isChecked}
                        onChange={(checked) =>
                          setSelectedFilters((prev) => ({ ...prev, [item.key]: checked }))
                        }
                        ariaLabel={item.name}
                      />
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: item.color.bg }} />
                      <span className="truncate text-[#3C4043] dark:text-[#E3E3E3] text-[12px]">{item.name}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          </aside>

          {/* ── GRID WORKSPACE (DAY / WEEK / MONTH / SCHEDULE) ── */}
          <div className={`flex min-w-0 flex-1 flex-col overflow-hidden ${mode === "month" ? "bg-[#F8FAFD] dark:bg-[#171920]" : "bg-white dark:bg-[#1E1F20]"}`}>
            {mode === "day" && (
              <DayGrid
                day={cursor}
                hours={hours}
                now={now}
                tasks={scheduledTasks}
                onSelect={openTaskDetails}
                onStartDrag={handleStartDragTime}
                onResizeDrag={handleResizeDragTime}
                dragSelection={dragSelection}
              />
            )}
            {mode === "week" && (
              <WeekGrid
                days={weekDays}
                hours={hours}
                now={now}
                tasks={scheduledTasks}
                onSelect={openTaskDetails}
                onStartDrag={handleStartDragTime}
                onResizeDrag={handleResizeDragTime}
                dragSelection={dragSelection}
                onOpenDay={(d) => {
                  setCursor(new Date(d));
                  setMode("day");
                }}
              />
            )}
            {mode === "month" && (
              <MonthGrid
                cursor={cursor}
                tasks={scheduledTasks}
                onSelect={openTaskDetails}
                onDoubleClickDay={(d) => openCreateForm(toDateString(d), "09:00", "10:00", "event")}
              />
            )}
            {mode === "schedule" && (
              <ScheduleView
                cursor={cursor}
                tasks={scheduledTasks}
                onSelect={openTaskDetails}
                onFinish={finish}
                onCreateNew={() => openCreateForm(toDateString(cursor))}
              />
            )}
          </div>
        </div>
      </main>

      {/* ── FULL EVENT / TASK MODAL ── */}
      {showForm && (
        <FullEventModal
          token={token}
          initialKind={newItemKind}
          initialDate={draftDate}
          initialStartTime={draftStartTime}
          initialEndTime={draftEndTime}
          initialTitle={draftTitle}
          initialLocation={draftLocation}
          initialColorId={draftColorId}
          initialIsAllDay={draftIsAllDay}
          onDraftChange={({ date, startTime, endTime, isAllDay }) => {
            setDragSelection((current) => {
              if (!current) return current;
              const start = isAllDay ? 0 : timeToMinutes(startTime);
              const end = isAllDay ? 1440 : Math.max(start + 15, timeToMinutes(endTime));
              if (current.dateStr === date && current.startMin === start && current.currentMin === end && !current.isDragging) return current;
              return { ...current, dateStr: date, startMin: start, currentMin: end, isDragging: false };
            });
          }}
          onClose={() => {
            setShowForm(false);
            setDraftTitle("");
            setDraftLocation("");
            setDragSelection(null);
          }}
          onSubmit={(task) => {
            setTasks((items) => [...items, task]);
            setShowForm(false);
            setDraftTitle("");
            setDraftLocation("");
            setDragSelection(null);
          }}
        />
      )}

      {editingTask && (
        <FullEventModal
          token={token}
          initialTask={editingTask}
          onClose={() => setEditingTask(null)}
          onSubmit={(task) => {
            setTasks((items) =>
              items.map((item) => (item.id === editingTask.id ? { ...task, id: editingTask.id } : item))
            );
            setEditingTask(null);
            setSelectedTask(null);
          }}
        />
      )}

      {/* ── EVENT DETAILS MODAL ── */}
      {selectedTask && (
        <EventDetailsModal
          task={selectedTask}
          anchor={detailAnchor}
          onClose={() => {
            setSelectedTask(null);
            setDetailAnchor(null);
          }}
          onFinish={finish}
          onEdit={() => {
            setEditingTask(selectedTask);
            setSelectedTask(null);
            setDetailAnchor(null);
          }}
          onDelete={() => deleteTask(selectedTask.id)}
        />
      )}
    </section>
  );
};

/* ────────────────────────── MINI CALENDAR COMPONENT ────────────────────────── */
const MiniCalendar: React.FC<{
  cursor: Date;
  onSelect: (date: Date) => void;
  now: Date;
  tasks: CalendarTask[];
}> = ({ cursor, onSelect, now, tasks }) => {
  const [viewMonth, setViewMonth] = useState(() => new Date(cursor.getFullYear(), cursor.getMonth(), 1));

  useEffect(() => {
    setViewMonth(new Date(cursor.getFullYear(), cursor.getMonth(), 1));
  }, [cursor]);

  const first = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - offset);
  const days = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });

  const hasTasks = (day: Date) => {
    const dStr = toDateString(day);
    return tasks.some((t) => (t.date ? t.date === dStr : sameDay(new Date(t.dueAt), day)) || matchesRecurrence(t, day));
  };

  return (
    <div className="rounded-2xl border border-[#DADCE0] bg-white p-3 dark:border-[#36373A] dark:bg-[#1E1F20]">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">
          {viewMonth.toLocaleDateString("vi-VN", { month: "long", year: "numeric" })}
        </span>
        <div className="flex gap-0.5">
          <button
            onClick={() =>
              setViewMonth((v) => {
                const d = new Date(v);
                d.setMonth(d.getMonth() - 1);
                return d;
              })
            }
            className="flex h-6 w-6 items-center justify-center rounded-full text-[#444746] hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#282A2C]"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() =>
              setViewMonth((v) => {
                const d = new Date(v);
                d.setMonth(d.getMonth() + 1);
                return d;
              })
            }
            className="flex h-6 w-6 items-center justify-center rounded-full text-[#444746] hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#282A2C]"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 text-center text-[10px] font-medium text-[#70757A] dark:text-[#9AA0A6]">
        {["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map((d) => (
          <div key={d} className="py-1">
            {d}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 text-center">
        {days.map((day) => {
          const isToday = sameDay(day, now);
          const isSelected = sameDay(day, cursor);
          const isCurrentMonth = day.getMonth() === viewMonth.getMonth();
          const hasEvent = hasTasks(day);

          return (
            <button
              key={day.toISOString()}
              onClick={() => onSelect(day)}
              className={`relative mx-auto flex h-6 w-6 items-center justify-center rounded-full text-[11px] transition-colors ${
                isToday
                  ? "bg-[#1A73E8] font-medium text-white dark:bg-[#A8C7FA] dark:text-[#131314]"
                  : isSelected
                  ? "bg-[#D3E3FD] font-medium text-[#0B57D0] dark:bg-[#004A77] dark:text-[#A8C7FA]"
                  : isCurrentMonth
                  ? "text-[#1F1F1F] hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#282A2C]"
                  : "text-[#9AA0A6] hover:bg-[#F1F3F4] dark:text-[#5F6368] dark:hover:bg-[#282A2C]"
              }`}
            >
              {day.getDate()}
              {hasEvent && !isToday && (
                <span className="absolute bottom-0.5 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-[#1A73E8] dark:bg-[#A8C7FA]" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};

/* ────────────────────────── WEEK GRID ────────────────────────── */
interface GridProps {
  hours: number[];
  now: Date;
  tasks: CalendarTask[];
  onSelect: (task: CalendarTask, anchor?: DetailPopoverAnchor) => void;
  onStartDrag: (dateStr: string, e: React.MouseEvent<HTMLDivElement>) => void;
  onResizeDrag: (dateStr: string, fixedMinute: number, e: React.MouseEvent<HTMLElement>) => void;
  dragSelection: DragTimeSelection | null;
}

function useScrollToNow(startHour: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const currentMins = new Date().getHours() * 60 + new Date().getMinutes();
    const target = ((currentMins - startHour * 60) / 60) * HOUR_HEIGHT - HOUR_HEIGHT * 1.5;
    node.scrollTop = Math.max(0, target);
  }, [startHour]);
  return ref;
}

const WeekGrid: React.FC<GridProps & { days: Date[]; onOpenDay: (day: Date) => void }> = ({
  days,
  hours,
  now,
  tasks,
  onSelect,
  onStartDrag,
  onResizeDrag,
  dragSelection,
  onOpenDay,
}) => {
  const startHour = hours[0];
  const gridHeight = hours.length * HOUR_HEIGHT;
  const scrollRef = useScrollToNow(startHour);

  const columns = useMemo(
    () =>
      days.map((day) => {
        const dayTasks = tasksOfDay(tasks, day);
        return {
          day,
          dateStr: toDateString(day),
          allDay: dayTasks.filter(isAllDayTask),
          timed: layoutDay(dayTasks, startHour),
        };
      }),
    [days, tasks, startHour]
  );

  const hasAllDay = columns.some((column) => column.allDay.length > 0);

  return (
    <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col overflow-auto pb-4 pr-4 scrollbar-thin">
      {/* Week Header Column Titles */}
      <div className="sticky top-0 z-20 min-w-[780px] border-b border-[#DADCE0] bg-white shadow-2xs dark:border-[#36373A] dark:bg-[#1E1F20]">
        <div className="grid grid-cols-[56px_repeat(7,minmax(100px,1fr))] text-center">
          <div className="flex items-end justify-center pb-2 text-[10px] font-medium text-[#70757A] border-r border-[#DADCE0] dark:border-[#36373A]">
            GMT+7
          </div>
          {days.map((day) => {
            const isToday = sameDay(day, now);
            return (
              <button
                key={day.toISOString()}
                type="button"
                onClick={() => onOpenDay(day)}
                className="border-r border-[#DADCE0] py-2 text-center transition-colors hover:bg-[#F8FAFD] last:border-r-0 dark:border-[#36373A] dark:hover:bg-[#282A2C]"
              >
                <div
                  className={`text-[11px] font-medium uppercase tracking-wider ${
                    isToday ? "text-[#1A73E8] font-bold dark:text-[#A8C7FA]" : "text-[#70757A] dark:text-[#9AA0A6]"
                  }`}
                >
                  {day.toLocaleDateString("vi-VN", { weekday: "short" })}
                </div>
                <span
                  className={`mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-full text-[24px] font-normal leading-none transition-colors ${
                    isToday
                      ? "bg-[#1A73E8] font-medium text-white shadow-sm dark:bg-[#A8C7FA] dark:text-[#131314]"
                      : "text-[#1F1F1F] hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#282A2C]"
                  }`}
                >
                  {day.getDate()}
                </span>
              </button>
            );
          })}
        </div>

        {/* Hàng Cả ngày (nếu có sự kiện cả ngày) */}
        {hasAllDay && (
          <div className="grid grid-cols-[56px_repeat(7,minmax(100px,1fr))] border-t border-[#DADCE0] dark:border-[#36373A]">
            <div className="flex items-start justify-end border-r border-[#DADCE0] px-2 py-1.5 text-[10px] font-medium text-[#70757A] dark:border-[#36373A]">
              Cả ngày
            </div>
            {columns.map(({ day, allDay }) => (
              <div
                key={day.toISOString()}
                className="space-y-1 border-r border-[#DADCE0] p-1 last:border-r-0 dark:border-[#36373A]"
              >
                {allDay.map((task) => (
                  <AllDayChip key={`${task.id}-${day.getDate()}`} task={task} onSelect={onSelect} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Grid Canvas: 24h slots */}
      <div className="grid min-w-[780px] grid-cols-[56px_repeat(7,minmax(100px,1fr))] border-b border-[#DADCE0] dark:border-[#36373A]">
        {/* Time Labels */}
        <div className="rounded-bl-2xl border-r border-[#DADCE0] bg-white select-none dark:border-[#36373A] dark:bg-[#1E1F20]">
          {hours.map((hour) => (
            <div key={hour} style={{ height: HOUR_HEIGHT }} className="relative">
              <span className="absolute -top-2.5 right-2 text-[10px] font-normal text-[#70757A] dark:text-[#9AA0A6]">
                {hour > 0 ? `${String(hour).padStart(2, "0")}:00` : ""}
              </span>
            </div>
          ))}
        </div>

        {/* 7 Day Columns với hỗ trợ kéo chọn khung giờ linh hoạt */}
        {columns.map(({ day, dateStr, timed }) => {
          const hasDraftSelection = dragSelection?.dateStr === dateStr;
          const dragStart = hasDraftSelection ? Math.min(dragSelection.startMin, dragSelection.currentMin) : 0;
          const dragEnd = hasDraftSelection ? Math.max(dragStart + 15, Math.max(dragSelection.startMin, dragSelection.currentMin)) : 0;
          const dragTop = hasDraftSelection ? (dragStart / 60) * HOUR_HEIGHT : 0;
          const dragHeight = hasDraftSelection ? Math.max(26, ((dragEnd - dragStart) / 60) * HOUR_HEIGHT) : 0;

          return (
            <div
              key={day.toISOString()}
              data-time-column
              onMouseDown={(e) => onStartDrag(dateStr, e)}
              className="relative border-r border-[#DADCE0] bg-white last:rounded-br-2xl last:border-r-0 cursor-crosshair dark:border-[#36373A] dark:bg-[#1E1F20]"
              style={{ height: gridHeight }}
            >
              {hours.map((hour) => (
                <div
                  key={hour}
                  style={{ height: HOUR_HEIGHT }}
                  className="group border-b border-[#DADCE0]/80 transition-colors hover:bg-[#F8FAFD]/60 dark:border-[#36373A]/80 dark:hover:bg-[#282A2C]/20 pointer-events-none"
                >
                  <div className="h-1/2 border-b border-dashed border-[#F1F3F4] dark:border-[#2D2E30]" />
                </div>
              ))}

              {/* Riêng lúc kéo thả trong khung Tuần: Vẫn hiển thị thời gian bắt đầu – kết thúc, bỏ chữ Chưa có tiêu đề */}
              {hasDraftSelection && (
                <div
                  style={{ top: dragTop, height: dragHeight }}
                  onMouseDown={(event) => event.stopPropagation()}
                  className="absolute inset-x-1 z-30 flex items-center justify-center rounded-md border-2 border-[#1A73E8] bg-[#1A73E8]/30 px-1 py-0.5 shadow-md backdrop-blur-[1px] animate-in fade-in-50 overflow-hidden"
                >
                  <button type="button" aria-label="Kéo để đổi giờ bắt đầu" onMouseDown={(event) => onResizeDrag(dateStr, dragEnd, event)} className="absolute inset-x-0 top-0 h-2 cursor-ns-resize" />
                  <span className="truncate rounded bg-white/95 px-1.5 py-0.5 text-[10px] sm:text-[11px] font-bold text-[#1A73E8] shadow-2xs">
                    {minutesToTime(dragStart)} – {minutesToTime(dragEnd)}
                  </span>
                  <button type="button" aria-label="Kéo để đổi giờ kết thúc" onMouseDown={(event) => onResizeDrag(dateStr, dragStart, event)} className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize" />
                </div>
              )}

              {/* Các sự kiện/việc cần làm trong tuần: hiển thị tiêu đề và thời gian đầu – kết thúc */}
              {timed.map((item) => (
                <TimedEventBlock
                  key={`${item.task.id}-${day.getDate()}`}
                  item={item}
                  onSelect={onSelect}
                />
              ))}

              {/* Red NowLine if today */}
              {sameDay(day, now) && <NowLine now={now} startHour={startHour} gridHeight={gridHeight} />}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ────────────────────────── DAY GRID ────────────────────────── */
const DayGrid: React.FC<GridProps & { day: Date }> = ({
  day,
  hours,
  now,
  tasks,
  onSelect,
  onStartDrag,
  onResizeDrag,
  dragSelection,
}) => {
  const startHour = hours[0];
  const gridHeight = hours.length * HOUR_HEIGHT;
  const scrollRef = useScrollToNow(startHour);
  const dateStr = toDateString(day);
  const dayTasks = tasksOfDay(tasks, day);
  const allDay = dayTasks.filter(isAllDayTask);
  const timed = layoutDay(dayTasks, startHour);
  const isToday = sameDay(day, now);

  const hasDraftSelection = dragSelection?.dateStr === dateStr;
  const dragStart = hasDraftSelection ? Math.min(dragSelection.startMin, dragSelection.currentMin) : 0;
  const dragEnd = hasDraftSelection ? Math.max(dragStart + 15, Math.max(dragSelection.startMin, dragSelection.currentMin)) : 0;
  const dragTop = hasDraftSelection ? (dragStart / 60) * HOUR_HEIGHT : 0;
  const dragHeight = hasDraftSelection ? Math.max(26, ((dragEnd - dragStart) / 60) * HOUR_HEIGHT) : 0;

  return (
    <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col overflow-auto scrollbar-thin">
      <div className="min-w-[540px]">
        {/* Day Header */}
        <div className="sticky top-0 z-20 border-b border-[#DADCE0] bg-white px-6 py-3 text-left dark:border-[#36373A] dark:bg-[#1E1F20]">
          <div
            data-time-column
            className={`text-xs font-semibold uppercase tracking-wider ${
              isToday ? "text-[#1A73E8] dark:text-[#A8C7FA]" : "text-[#70757A] dark:text-[#9AA0A6]"
            }`}
          >
            {day.toLocaleDateString("vi-VN", { weekday: "long" })}
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`mt-0.5 inline-flex h-11 w-11 items-center justify-center rounded-full text-[28px] font-normal leading-none ${
                isToday
                  ? "bg-[#1A73E8] font-medium text-white shadow-sm dark:bg-[#A8C7FA] dark:text-[#131314]"
                  : "text-[#1F1F1F] dark:text-[#E3E3E3]"
              }`}
            >
              {day.getDate()}
            </span>
            <span className="text-sm text-[#70757A] dark:text-[#9AA0A6]">
              {day.toLocaleDateString("vi-VN", { month: "long", year: "numeric" })}
            </span>
          </div>

          {allDay.length > 0 && (
            <div className="mt-3 flex items-start gap-3 border-t border-[#DADCE0] pt-2 dark:border-[#36373A]">
              <span className="pt-1 text-[11px] font-medium uppercase tracking-wide text-[#70757A]">Cả ngày</span>
              <div className="flex flex-1 flex-wrap gap-1.5">
                {allDay.map((task) => (
                  <AllDayChip key={task.id} task={task} onSelect={onSelect} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 24h Time Grid với kéo chọn linh hoạt */}
        <div className="grid grid-cols-[56px_minmax(0,1fr)]">
          <div className="border-r border-[#DADCE0] bg-white select-none dark:border-[#36373A] dark:bg-[#1E1F20]">
            {hours.map((hour) => (
              <div key={hour} style={{ height: HOUR_HEIGHT }} className="relative">
                <span className="absolute -top-2.5 right-2 text-[10px] font-normal text-[#70757A] dark:text-[#9AA0A6]">
                  {hour > 0 ? `${String(hour).padStart(2, "0")}:00` : ""}
                </span>
              </div>
            ))}
          </div>

          <div
            onMouseDown={(e) => onStartDrag(dateStr, e)}
            className="relative bg-white cursor-crosshair dark:bg-[#1E1F20]"
            style={{ height: gridHeight }}
          >
            {hours.map((hour) => (
              <div
                key={hour}
                style={{ height: HOUR_HEIGHT }}
                className="border-b border-[#DADCE0]/80 transition-colors hover:bg-[#F8FAFD]/60 dark:border-[#36373A]/80 dark:hover:bg-[#282A2C]/20 pointer-events-none"
              >
                <div className="h-1/2 border-b border-dashed border-[#F1F3F4] dark:border-[#2D2E30]" />
              </div>
            ))}

            {/* Khung xem trước kéo chọn giờ: Hiển thị đầy đủ chi tiết */}
            {hasDraftSelection && (
              <div
                style={{ top: dragTop, height: dragHeight }}
                onMouseDown={(event) => event.stopPropagation()}
                className="absolute inset-x-2 z-30 flex flex-col justify-start rounded-md border-2 border-[#1A73E8] bg-[#1A73E8]/25 p-2 shadow-md backdrop-blur-[1px] animate-in fade-in-50 overflow-hidden"
              >
                <button type="button" aria-label="Kéo để đổi giờ bắt đầu" onMouseDown={(event) => onResizeDrag(dateStr, dragEnd, event)} className="absolute inset-x-0 top-0 h-2 cursor-ns-resize" />
                <div className="flex items-center justify-between gap-2 leading-tight text-xs font-semibold text-[#0B57D0] dark:text-[#A8C7FA]">
                  <span className="truncate text-xs font-semibold">(Chưa có tiêu đề)</span>
                  <span className="shrink-0 rounded bg-white px-2 py-0.5 text-xs font-bold text-[#1A73E8] shadow-2xs">
                    {minutesToTime(dragStart)} – {minutesToTime(dragEnd)}
                  </span>
                </div>

                {dragHeight >= 36 && (
                  <div className="mt-1 flex items-center gap-1.5 text-xs font-medium text-[#1A73E8] dark:text-[#A8C7FA] truncate">
                    <Clock className="h-3.5 w-3.5 shrink-0" />
                    <span>Thời lượng: {formatMinutesDuration(dragEnd - dragStart)}</span>
                  </div>
                )}
                <button type="button" aria-label="Kéo để đổi giờ kết thúc" onMouseDown={(event) => onResizeDrag(dateStr, dragStart, event)} className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize" />
              </div>
            )}

            {/* Sự kiện (Tự động dàn đều song song khi trùng giờ) */}
            {timed.map((item) => (
              <TimedEventBlock key={item.task.id} item={item} onSelect={onSelect} />
            ))}

            {isToday && <NowLine now={now} startHour={startHour} gridHeight={gridHeight} />}
          </div>
        </div>
      </div>
    </div>
  );
};

/* ────────────────────────── MONTH GRID ────────────────────────── */
const MonthGrid: React.FC<{
  cursor: Date;
  tasks: CalendarTask[];
  onSelect: (task: CalendarTask, anchor?: DetailPopoverAnchor) => void;
  onDoubleClickDay: (date: Date) => void;
}> = ({ cursor, tasks, onSelect, onDoubleClickDay }) => {
  const today = new Date();
  const [expandedDay, setExpandedDay] = useState<Date | null>(null);

  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - offset);

  const days = Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });

  return (
    <div className="mr-4 mb-4 flex min-h-0 flex-1 flex-col overflow-auto rounded-b-xl border-b border-r border-[#DADCE0] bg-white scrollbar-thin dark:border-[#36373A] dark:bg-[#1E1F20]">
      {/* Month Days of Week Header */}
      <div className="grid min-w-[700px] grid-cols-7 border-b border-[#DADCE0] bg-white dark:border-[#36373A] dark:bg-[#1E1F20]">
        {["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map((day) => (
          <div
            key={day}
            className="py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-[#70757A] dark:text-[#9AA0A6]"
          >
            {day}
          </div>
        ))}
      </div>

      {/* Month Grid Cells */}
      <div className="grid min-h-[620px] min-w-[700px] flex-1 grid-cols-7 grid-rows-6">
        {days.map((day) => {
          const items = tasksOfDay(tasks, day);
          const isCurrentMonth = day.getMonth() === cursor.getMonth();
          const isToday = sameDay(day, today);

          return (
            <div
              key={day.toISOString()}
              onDoubleClick={() => onDoubleClickDay(day)}
              className={`relative border-b border-r border-[#DADCE0] p-1.5 transition-colors hover:bg-[#F8FAFD] dark:border-[#36373A] dark:hover:bg-[#282A2C]/30 ${
                !isCurrentMonth ? "bg-[#F8F9FA] dark:bg-[#171920]" : "bg-white dark:bg-[#1E1F20]"
              }`}
            >
              {/* Day Header Badge */}
              <div className="flex items-center justify-between">
                <span
                  className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs transition-colors ${
                    isToday
                      ? "bg-[#1A73E8] font-medium text-white dark:bg-[#A8C7FA] dark:text-[#131314]"
                      : isCurrentMonth
                      ? "font-medium text-[#3C4043] dark:text-[#E3E3E3]"
                      : "text-[#80868B] dark:text-[#5F6368]"
                  }`}
                >
                  {day.getDate()}
                </span>
                {items.length > 0 && (
                  <span className="text-[10px] text-[#70757A] font-medium">
                    {items.length} mục
                  </span>
                )}
              </div>

              {/* Day's Event Chips */}
              <div className="mt-1 space-y-1">
                {items.slice(0, 3).map((task) => {
                  const isAllDay = isAllDayTask(task);
                  const color = getEventColor(task);
                  const isTask = task.kind === "task";

                  return isAllDay ? (
                    <button
                      key={`${task.id}-${day.getDate()}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        const rect = e.currentTarget.getBoundingClientRect();
                        onSelect(task, { top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left });
                      }}
                      style={{ backgroundColor: color.bg }}
                      className="flex w-full items-center gap-1 truncate rounded px-1.5 py-0.5 text-left text-[11px] font-medium text-white transition-all hover:brightness-105"
                    >
                      {isTask && <CheckCircle2 className="h-3 w-3 shrink-0" />}
                      <span className="truncate flex-1">{task.title}</span>
                    </button>
                  ) : (
                    <button
                      key={`${task.id}-${day.getDate()}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        const rect = e.currentTarget.getBoundingClientRect();
                        onSelect(task, { top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left });
                      }}
                      className="flex w-full items-center gap-1.5 truncate rounded px-1 py-0.5 text-left text-[11px] transition-colors hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
                    >
                      <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color.bg }} />
                      <span className="font-semibold text-[#3C4043] dark:text-[#E3E3E3]">
                        {task.startTime || new Date(task.dueAt).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
                      </span>
                      <span className={`truncate text-[#3C4043] dark:text-[#C4C7C5] flex-1 ${task.status === "done" ? "line-through opacity-60" : ""}`}>
                        {task.title}
                      </span>
                    </button>
                  );
                })}

                {items.length > 3 && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedDay(new Date(day));
                    }}
                    className="rounded px-1.5 text-[11px] font-medium text-[#1A73E8] hover:bg-[#E8F0FE] hover:underline dark:text-[#A8C7FA]"
                  >
                    +{items.length - 3} mục khác
                  </button>
                )}
              </div>

              {/* Month Day Expanded Modal */}
              {expandedDay && sameDay(expandedDay, day) && (
                <div
                  role="dialog"
                  className="absolute left-1 top-8 z-30 w-72 overflow-hidden rounded-2xl border border-[#DADCE0] bg-white shadow-2xl dark:border-[#5F6368] dark:bg-[#2D2E30]"
                >
                  <div className="flex items-center justify-between border-b border-[#DADCE0] px-3.5 py-2.5 dark:border-[#5F6368]">
                    <span className="text-xs font-semibold text-[#1F1F1F] dark:text-[#E3E3E3]">
                      Lịch ngày {day.toLocaleDateString("vi-VN", { day: "numeric", month: "long" })}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedDay(null);
                      }}
                      className="rounded-full p-1 text-[#70757A] hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="max-h-64 overflow-y-auto p-2 space-y-1">
                    {items.map((task) => {
                      const color = getEventColor(task);
                      return (
                        <button
                          key={task.id}
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedDay(null);
                            onSelect(task);
                          }}
                          className="flex w-full items-center gap-2 rounded-xl p-2 text-left text-xs transition-colors hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
                        >
                          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: color.bg }} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">{task.title}</p>
                            <p className="text-[10px] text-[#70757A] dark:text-[#9AA0A6]">
                              {isAllDayTask(task) ? "Cả ngày" : `${task.startTime} – ${task.endTime}`}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ────────────────────────── SCHEDULE (AGENDA) VIEW ────────────────────────── */
const ScheduleView: React.FC<{
  cursor: Date;
  tasks: CalendarTask[];
  onSelect: (task: CalendarTask) => void;
  onFinish: (task: CalendarTask) => void;
  onCreateNew: () => void;
}> = ({ cursor, tasks, onSelect, onFinish, onCreateNew }) => {
  const daysList = useMemo(() => {
    const list: { date: Date; dateStr: string; items: CalendarTask[] }[] = [];
    const base = new Date(cursor);
    base.setDate(base.getDate() - 3);

    for (let i = 0; i < 21; i++) {
      const d = new Date(base);
      d.setDate(base.getDate() + i);
      const dayItems = tasksOfDay(tasks, d);
      if (dayItems.length > 0) {
        list.push({ date: d, dateStr: toDateString(d), items: dayItems });
      }
    }
    return list;
  }, [cursor, tasks]);

  const today = new Date();

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6 scrollbar-thin">
      <div className="mx-auto w-full max-w-4xl space-y-6">
        <div className="flex items-center justify-between border-b border-[#DADCE0] pb-3 dark:border-[#36373A]">
          <h3 className="text-base font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">
            Danh sách Lịch biểu
          </h3>
          <button
            onClick={onCreateNew}
            className="flex items-center gap-1.5 rounded-full bg-[#1A73E8] px-4 py-1.5 text-xs font-medium text-white hover:bg-[#1557B0]"
          >
            <Plus className="h-4 w-4" /> Thêm lịch mới
          </button>
        </div>

        {daysList.length === 0 ? (
          <div className="py-16 text-center text-[#70757A]">
            <CalendarDays className="mx-auto mb-3 h-12 w-12 text-[#DADCE0] dark:text-[#444746]" />
            <p className="text-sm font-medium">Không có sự kiện hoặc việc cần làm nào</p>
            <p className="text-xs mt-1">Nhấp "+ Thêm lịch mới" để tạo.</p>
          </div>
        ) : (
          daysList.map(({ date, items }) => {
            const isToday = sameDay(date, today);
            return (
              <div key={date.toISOString()} className="space-y-3">
                {/* Date Header Badge */}
                <div className="flex items-center gap-3">
                  <div
                    className={`flex h-10 w-10 flex-col items-center justify-center rounded-2xl border ${
                      isToday
                        ? "border-[#1A73E8] bg-[#1A73E8] text-white"
                        : "border-[#DADCE0] bg-white text-[#1F1F1F] dark:border-[#36373A] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
                    }`}
                  >
                    <span className="text-[10px] uppercase font-bold">{date.toLocaleDateString("vi-VN", { weekday: "short" })}</span>
                    <span className="text-sm font-bold leading-tight">{date.getDate()}</span>
                  </div>
                  <div>
                    <h4 className="font-semibold text-sm text-[#1F1F1F] dark:text-[#E3E3E3]">
                      {formatVietnameseDate(date, true)}
                    </h4>
                    <span className="text-[11px] text-[#70757A]">
                      {items.length} mục trong ngày
                    </span>
                  </div>
                </div>

                {/* Items in that day */}
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {items.map((task) => {
                    const color = getEventColor(task);
                    const isTask = task.kind === "task";
                    return (
                      <article
                        key={task.id}
                        onClick={() => onSelect(task)}
                        className="group cursor-pointer rounded-2xl border border-[#DADCE0] bg-white p-4 transition-all hover:border-[#1A73E8] hover:shadow-md dark:border-[#36373A] dark:bg-[#1E1F20] dark:hover:border-[#A8C7FA]"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-start gap-2.5 min-w-0">
                            {isTask ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onFinish(task);
                                }}
                                className="mt-0.5 text-[#1A73E8]"
                              >
                                <CheckCircle2 className={`h-4 w-4 ${task.status === "done" ? "fill-[#1A73E8] text-white" : "text-[#70757A]"}`} />
                              </button>
                            ) : (
                              <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: color.bg }} />
                            )}
                            <div className="min-w-0 flex-1">
                              <h5 className={`text-sm font-medium text-[#1F1F1F] dark:text-[#E3E3E3] truncate ${task.status === "done" ? "line-through opacity-60" : ""}`}>
                                {task.title}
                              </h5>
                              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#70757A] dark:text-[#9AA0A6]">
                                <span className="flex items-center gap-1">
                                  <Clock className="h-3.5 w-3.5 text-[#1A73E8]" />
                                  {isAllDayTask(task) ? "Cả ngày" : `${task.startTime} – ${task.endTime}`}
                                </span>
                                {task.duration && !isAllDayTask(task) && <span>({formatMinutesDuration(task.duration)})</span>}
                              </div>
                            </div>
                          </div>

                          <span
                            className="rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0"
                            style={{ backgroundColor: color.light, color: color.bg }}
                          >
                            {task.kind === "task" ? "Việc cần làm" : "Sự kiện"}
                          </span>
                        </div>

                        {/* Location and attendees */}
                        {(task.location || (task.attendees && task.attendees.length > 0)) && (
                          <div className="mt-3 flex flex-wrap items-center gap-2 pt-2 border-t border-[#F1F3F4] text-xs dark:border-[#2D2E30]">
                            {task.location && (
                              <span className="inline-flex items-center gap-1 text-[#70757A] dark:text-[#9AA0A6] text-[11px] truncate max-w-[200px]">
                                <MapPin className="h-3.5 w-3.5" /> {task.location}
                              </span>
                            )}
                            {task.attendees && task.attendees.length > 0 && (
                              <span className="inline-flex items-center gap-1 text-[#70757A] text-[11px]">
                                <Users className="h-3.5 w-3.5" /> {task.attendees.length} người
                              </span>
                            )}
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

/* ────────────────────────── TIMED EVENT BLOCK (WEEK / DAY) ────────────────────────── */
const TimedEventBlock: React.FC<{
  item: PositionedTask;
  onSelect: (task: CalendarTask, anchor?: DetailPopoverAnchor) => void;
}> = ({ item, onSelect }) => {
  const { task } = item;
  const color = getEventColor(task);
  const isTask = task.kind === "task";
  const isShort = item.height < 36;
  const timeRange = `${task.startTime || "00:00"} – ${task.endTime || "00:00"}`;

  return (
    <button
      data-task-block="true"
      onClick={(e) => {
        e.stopPropagation();
        const rect = e.currentTarget.getBoundingClientRect();
        onSelect(task, { top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left });
      }}
      style={{
        top: item.top + 1,
        height: Math.max(22, item.height),
        left: `calc(${item.left}% + 1px)`,
        width: `calc(${item.width}% - 2px)`,
        backgroundColor: color.bg,
      }}
      className={`absolute overflow-hidden rounded-md px-1.5 py-0.5 text-left text-white shadow-2xs ring-1 ring-white/90 transition-all hover:z-30 hover:brightness-105 hover:shadow-md select-none dark:ring-[#1E1F20] ${
        task.status === "done" ? "opacity-60" : ""
      }`}
    >
      {isShort ? (
        /* Sự kiện ngắn (15 - 30p): Tiêu đề và giờ trên cùng 1 dòng liền mạch */
        <div className="flex h-full items-center gap-1 overflow-hidden text-[10px] sm:text-[11px] leading-none">
          {isTask && <CheckCircle2 className="h-3 w-3 shrink-0 opacity-90" />}
          <span className="shrink-0 font-medium tracking-tight opacity-95">
            {timeRange}
          </span>
          <span className="opacity-40">•</span>
          <span className={`truncate font-semibold flex-1 ${task.status === "done" ? "line-through opacity-60" : ""}`}>
            {task.title}
          </span>
        </div>
      ) : (
        /* Sự kiện từ 45p trở lên: Hàng 1 Tiêu đề, Hàng 2 Thời gian đầu – đến trên cùng 1 hàng */
        <div className="flex h-full flex-col justify-start gap-0.5 overflow-hidden">
          <div className="flex items-center gap-1 leading-tight">
            {isTask && <CheckCircle2 className="h-3 w-3 shrink-0 opacity-90" />}
            <span className={`truncate text-[11px] sm:text-xs font-semibold flex-1 ${task.status === "done" ? "line-through opacity-60" : ""}`}>
              {task.title}
            </span>
          </div>
          <div className="truncate text-[10px] sm:text-[11px] font-medium tracking-tight opacity-95 leading-none">
            {timeRange}
          </div>
        </div>
      )}
    </button>
  );
};

/* ────────────────────────── ALL-DAY CHIP ────────────────────────── */
const AllDayChip: React.FC<{
  task: CalendarTask;
  onSelect: (task: CalendarTask, anchor?: DetailPopoverAnchor) => void;
}> = ({ task, onSelect }) => {
  const color = getEventColor(task);
  const isTask = task.kind === "task";

  return (
    <button
      data-task-block="true"
      onClick={(e) => {
        e.stopPropagation();
        const rect = e.currentTarget.getBoundingClientRect();
        onSelect(task, { top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left });
      }}
      style={{ backgroundColor: color.bg }}
      className={`flex w-full items-center gap-1 truncate rounded-md px-2 py-1 text-left text-[11px] font-medium text-white transition-all hover:brightness-105 hover:shadow-xs ${
        task.status === "done" ? "opacity-60 line-through" : ""
      }`}
    >
      {isTask && <CheckCircle2 className="h-3 w-3 shrink-0" />}
      <span className="truncate flex-1">{task.title}</span>
    </button>
  );
};

/* ────────────────────────── NOW LINE ────────────────────────── */
const NowLine: React.FC<{ now: Date; startHour: number; gridHeight: number }> = ({ now, startHour, gridHeight }) => {
  const currentMins = now.getHours() * 60 + now.getMinutes();
  const offset = ((currentMins - startHour * 60) / 60) * HOUR_HEIGHT;
  if (offset < 0 || offset > gridHeight) return null;

  return (
    <div
      aria-label="Thời điểm hiện tại"
      className="pointer-events-none absolute inset-x-0 z-20 flex items-center"
      style={{ top: offset }}
    >
      <span className="ml-[-6px] h-3.5 w-3.5 rounded-full bg-[#EA4335] shadow-[0_0_6px_rgba(234,67,53,0.8)]" />
      <span className="h-[2px] flex-1 bg-[#EA4335]" />
    </div>
  );
};

/* ────────────────────────── QUICK CREATE POPOVER ────────────────────────── */
const QuickCreatePopover: React.FC<{
  dateStr: string;
  startTime: string;
  endTime: string;
  anchor: DetailPopoverAnchor | null;
  onClose: () => void;
  onMoreOptions: (title: string, kind: EventKind, colorId: string, location: string, isAllDay: boolean) => void;
  onSave: (task: CalendarTask) => void;
}> = ({ dateStr, startTime, endTime, anchor, onClose, onMoreOptions, onSave }) => {
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<EventKind>("event");
  const [isAllDay, setIsAllDay] = useState(false);
  const [location, setLocation] = useState("");
  const [colorId, setColorId] = useState<string>("sage");
  const [showColorPalette, setShowColorPalette] = useState(false);
  const [note, setNote] = useState("");
  const dateObj = parseDateString(dateStr);

  const startMins = timeToMinutes(startTime);
  const endMins = timeToMinutes(endTime);
  const duration = isAllDay ? 1440 : Math.max(15, endMins - startMins);

  const handleQuickSave = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!title.trim()) return;

    const newTask: CalendarTask = {
      id: `task-${Date.now()}`,
      title: title.trim(),
      kind,
      date: dateStr,
      isAllDay,
      startTime: isAllDay ? undefined : startTime,
      endTime: isAllDay ? undefined : endTime,
      duration,
      dueAt: buildSingleDayIso(dateStr, isAllDay ? "00:00" : startTime),
      endAt: buildSingleDayIso(dateStr, isAllDay ? "23:59" : endTime),
      recurrence: "none",
      colorId,
      category: kind === "task" ? "task" : "meeting",
      location: kind === "event" && location.trim() ? location.trim() : undefined,
      note: note.trim() || undefined,
      reminders: [{ id: "r1", method: "popup", minutes: 10 }],
      status: "approved",
      source: "manual",
    };

    onSave(newTask);
  };

  return (
    <Modal title="" maxWidth="max-w-md" popoverAnchor={anchor} onClose={onClose}>
      <form onSubmit={handleQuickSave} className="space-y-3.5">
        {/* Title Input */}
        <input
          autoFocus
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={kind === "task" ? "Thêm tiêu đề việc cần làm" : "Thêm tiêu đề sự kiện"}
          className="w-full border-0 border-b-2 border-[#1A73E8] bg-transparent px-0 py-1.5 text-base font-medium text-[#1F1F1F] outline-none placeholder:text-[#9AA0A6] dark:text-[#E3E3E3]"
        />

        {/* 2 Lựa chọn: Sự kiện & Việc cần làm */}
        <div className="flex gap-1 rounded-xl bg-[#F1F3F4] p-1 dark:bg-[#2D2E30]">
          <button
            type="button"
            onClick={() => {
              setKind("event");
              if (colorId === "peacock") setColorId("sage");
            }}
            className={`flex-1 rounded-lg py-1.5 text-xs font-medium transition-all ${
              kind === "event"
                ? "bg-white text-[#1A73E8] shadow-xs dark:bg-[#444746] dark:text-[#A8C7FA]"
                : "text-[#5F6368] dark:text-[#C4C7C5]"
            }`}
          >
            📅 Sự kiện
          </button>
          <button
            type="button"
            onClick={() => {
              setKind("task");
              if (colorId === "sage") setColorId("peacock");
            }}
            className={`flex-1 rounded-lg py-1.5 text-xs font-medium transition-all ${
              kind === "task"
                ? "bg-white text-[#1A73E8] shadow-xs dark:bg-[#444746] dark:text-[#A8C7FA]"
                : "text-[#5F6368] dark:text-[#C4C7C5]"
            }`}
          >
            🎯 Việc cần làm
          </button>
        </div>

        {/* Single-Day Info Badge & Cả ngày Checkbox */}
        <div className="flex items-center justify-between gap-2 rounded-xl bg-[#F8FAFD] p-2.5 text-xs text-[#3C4043] dark:bg-[#1E1F20] dark:text-[#C4C7C5]">
          <div className="flex items-center gap-2 min-w-0">
            <Clock3 className="h-4 w-4 text-[#1A73E8] shrink-0" />
            <div className="min-w-0 truncate">
              <span className="font-medium">{formatVietnameseDate(dateObj, true)}</span>
              {!isAllDay && <span className="ml-1.5 font-semibold text-[#1A73E8] dark:text-[#A8C7FA]">({startTime} – {endTime})</span>}
              {!isAllDay && <span className="ml-1 text-[11px] text-[#70757A]">({formatMinutesDuration(duration)})</span>}
            </div>
          </div>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-[#1F1F1F] dark:text-[#E3E3E3] shrink-0">
            <CustomCheckbox checked={isAllDay} onChange={setIsAllDay} />
            <span>Cả ngày</span>
          </label>
        </div>

        {/* Địa điểm (cho Sự kiện). */}
        {kind === "event" && (
          <div className="flex items-center gap-2 rounded-xl border border-[#DADCE0] bg-white px-2.5 py-1.5 text-xs dark:border-[#5F6368] dark:bg-[#2D2E30]">
            <MapPin className="h-3.5 w-3.5 text-[#70757A] shrink-0" />
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Thêm vị trí, phòng họp..."
              className="w-full bg-transparent text-xs text-[#1F1F1F] outline-none dark:text-[#E3E3E3]"
            />
          </div>
        )}

        <div className="relative flex items-center gap-2">
          <span className="text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5]">Màu sắc</span>
          <button type="button" onClick={() => setShowColorPalette((current) => !current)} aria-label="Chọn màu sắc" aria-expanded={showColorPalette} className="flex h-8 items-center gap-1.5 rounded-lg border border-[#DADCE0] bg-white px-2 hover:bg-[#F8FAFD] dark:border-[#5F6368] dark:bg-[#2D2E30]">
            <span className="h-4 w-4 rounded-full" style={{ backgroundColor: CALENDAR_PALETTE[colorId]?.bg }} />
            <ChevronDown className="h-3.5 w-3.5 text-[#70757A]" />
          </button>
          {showColorPalette && <div className="absolute left-0 top-[calc(100%+0.5rem)] z-20 grid w-52 grid-cols-6 gap-2 rounded-xl border border-[#DADCE0] bg-white p-3 shadow-lg dark:border-[#5F6368] dark:bg-[#2D2E30]">
            {Object.values(CALENDAR_PALETTE).map((color) => <button key={color.id} type="button" title={color.name} onClick={() => { setColorId(color.id); setShowColorPalette(false); }} className={`grid h-6 w-6 place-items-center rounded-full transition-transform hover:scale-110 ${colorId === color.id ? "ring-2 ring-[#1A73E8] ring-offset-2 dark:ring-offset-[#2D2E30]" : ""}`} style={{ backgroundColor: color.bg }}>{colorId === color.id && <Check className="h-3.5 w-3.5 text-white stroke-[3]" />}</button>)}
          </div>}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between pt-2 border-t border-[#DADCE0] dark:border-[#36373A]">
          <button
            type="button"
            onClick={() => onMoreOptions(title, kind, colorId, location, isAllDay)}
            className="text-xs font-medium text-[#1A73E8] hover:underline dark:text-[#A8C7FA]"
          >
            Tùy chọn khác
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full px-4 py-1.5 text-xs font-medium text-[#70757A] hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
            >
              Huỷ
            </button>
            <button
              type="submit"
              disabled={!title.trim()}
              className="rounded-full bg-[#1A73E8] px-5 py-1.5 text-xs font-medium text-white transition-all hover:bg-[#1557B0] disabled:opacity-50"
            >
              Lưu
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
};

/* ────────────────────────── FULL EVENT / TASK MODAL ────────────────────────── */
interface FullEventModalProps {
  token: string;
  popoverAnchor?: DetailPopoverAnchor | null;
  initialTask?: CalendarTask;
  initialKind?: EventKind;
  initialDate?: string;
  initialStartTime?: string;
  initialEndTime?: string;
  initialTitle?: string;
  initialLocation?: string;
  initialNote?: string;
  initialColorId?: string;
  initialIsAllDay?: boolean;
  submitLabel?: string;
  reviewWarning?: string;
  onDraftChange?: (draft: { date: string; startTime: string; endTime: string; isAllDay: boolean }) => void;
  onClose: () => void;
  onSubmit: (task: CalendarTask) => void;
}

export const FullEventModal: React.FC<FullEventModalProps> = ({
  token,
  popoverAnchor = null,
  initialTask,
  initialKind = "event",
  initialDate = toDateString(new Date()),
  initialStartTime = "09:00",
  initialEndTime = "10:00",
  initialTitle = "",
  initialLocation = "",
  initialNote = "",
  initialColorId,
  initialIsAllDay = false,
  submitLabel,
  reviewWarning,
  onDraftChange,
  onClose,
  onSubmit,
}) => {
  const [kind, setKind] = useState<EventKind>(initialTask?.kind ?? initialKind);
  const [title, setTitle] = useState(initialTask?.title ?? initialTitle);
  const [timeChanged, setTimeChanged] = useState(false);

  // ── RÀNG BUỘC: 1 NGÀY DUY NHẤT VỚI LỰA CHỌN CẢ NGÀY ──
  const [date, setDate] = useState<string>(
    initialTask?.date || (initialTask?.dueAt ? toDateString(new Date(initialTask.dueAt)) : initialDate)
  );
  const [isAllDay, setIsAllDay] = useState<boolean>(initialTask?.isAllDay ?? initialIsAllDay);

  // Giờ bắt đầu & Giờ kết thúc
  const [startTime, setStartTime] = useState<string>(
    initialTask?.startTime || (initialTask?.dueAt ? toTimeString(new Date(initialTask.dueAt)) : initialStartTime)
  );
  const [endTime, setEndTime] = useState<string>(() => {
    const start = initialTask?.startTime || (initialTask?.dueAt ? toTimeString(new Date(initialTask.dueAt)) : initialStartTime);
    const end = initialTask?.endTime || (initialTask?.endAt ? toTimeString(new Date(initialTask.endAt)) : initialEndTime);
    return ensureEndTimeAfterStart(start, end);
  });

  // Lặp lại (Đã sửa: "Thứ 2 đến Thứ 6")
  const [recurrence, setRecurrence] = useState<RecurrenceFreq>(initialTask?.recurrence ?? "none");

  // Địa điểm (chỉ dùng cho Sự kiện)
  const [location, setLocation] = useState(initialTask?.location ?? initialLocation);

  // Người tham gia
  const [attendees, setAttendees] = useState<Attendee[]>(initialTask?.attendees ?? []);
  const [guestEmail, setGuestEmail] = useState("");
  const [guestError, setGuestError] = useState("");
  const [suggestedUsers, setSuggestedUsers] = useState<ApiUser[]>([]);
  const [isSearchingGuests, setIsSearchingGuests] = useState(false);
  const [groups, setGroups] = useState<ApiConversation[]>([]);
  const [inviteMode, setInviteMode] = useState<"person" | "group">("person");
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [isLoadingGroups, setIsLoadingGroups] = useState(false);
  const [groupError, setGroupError] = useState("");

  // Màu sắc cho sự kiện
  const [colorId, setColorId] = useState<string>(
    initialTask?.colorId ?? initialColorId ?? (kind === "task" ? "peacock" : "sage")
  );
  const [showColorPalette, setShowColorPalette] = useState(false);

  // Nhắc nhở (Thông báo)
  const [reminders, setReminders] = useState<EventReminder[]>(
    initialTask?.reminders && initialTask.reminders.length > 0
      ? initialTask.reminders
      : [{ id: "r1", method: "popup", minutes: 10 }]
  );
  const [reminderUnits, setReminderUnits] = useState<Record<string, ReminderUnit>>(() =>
    Object.fromEntries(
      (initialTask?.reminders ?? [{ id: "r1", method: "popup", minutes: 10 }]).map((reminder) => [
        reminder.id,
        reminder.minutes > 0 && reminder.minutes % 1440 === 0
          ? "days"
          : reminder.minutes > 0 && reminder.minutes % 60 === 0
            ? "hours"
            : "minutes",
      ]),
    ),
  );

  // Ghi chú / Mô tả
  const [note, setNote] = useState(initialTask?.note ?? initialNote);

  useEffect(() => {
    onDraftChange?.({ date, startTime, endTime, isAllDay });
  }, [date, endTime, isAllDay, onDraftChange, startTime]);

  // Tính thời lượng
  const calculatedDuration = useMemo(() => {
    if (isAllDay) return 1440;
    const startMins = timeToMinutes(startTime);
    const endMins = timeToMinutes(endTime);
    return Math.max(15, endMins - startMins);
  }, [isAllDay, startTime, endTime]);

  // Điều chỉnh giờ kết thúc tự động khi đổi giờ bắt đầu
  const handleStartTimeChange = (newStart: string) => {
    setTimeChanged(true);
    setStartTime(newStart);
    setEndTime(ensureEndTimeAfterStart(newStart, endTime));
  };

  const handleEndTimeChange = (newEnd: string) => {
    setTimeChanged(true);
    setEndTime(ensureEndTimeAfterStart(startTime, newEnd));
  };

  useEffect(() => {
    const query = guestEmail.trim();
    if (query.length < 2) {
      setSuggestedUsers([]);
      setIsSearchingGuests(false);
      return;
    }

    let active = true;
    const timer = window.setTimeout(() => {
      setIsSearchingGuests(true);
      void listUsers(token, query)
        .then((users) => {
          if (!active) return;
          setSuggestedUsers(
            users
              .filter((user) => !attendees.some((attendee) => attendee.email.toLowerCase() === user.email.toLowerCase()))
              .slice(0, 5),
          );
        })
        .catch(() => {
          if (active) setSuggestedUsers([]);
        })
        .finally(() => {
          if (active) setIsSearchingGuests(false);
        });
    }, 250);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [attendees, guestEmail, token]);

  const handleAddGuest = (e?: React.FormEvent, value = guestEmail) => {
    if (e) e.preventDefault();
    const clean = value.trim();
    if (!clean) return;

    const normalizedEmail = clean.toLowerCase();
    const isValidEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail);
    if (!isValidEmail) {
      setGuestError("Nhập địa chỉ email hợp lệ.");
      return;
    }
    if (attendees.some((attendee) => attendee.email.toLowerCase() === normalizedEmail)) {
      setGuestError("Email này đã có trong danh sách.");
      return;
    }

    setAttendees((current) => [
      ...current,
      {
        email: normalizedEmail,
        name: normalizedEmail.split("@")[0],
        status: "needsAction",
      },
    ]);
    setGuestEmail("");
    setGuestError("");
    setSuggestedUsers([]);
  };

  const handleRemoveGuest = (email: string) => {
    setAttendees((current) => current.filter((attendee) => attendee.email !== email));
  };

  const handleInviteModeChange = async (nextMode: "person" | "group") => {
    setInviteMode(nextMode);
    setGroupError("");
    if (nextMode === "person") return;
    setIsLoadingGroups(true);
    try {
      const conversations = await listConversations(token);
      setGroups(
        conversations.filter(
          (conversation) => conversation.type === "group" && conversation.members.length > 2,
        ),
      );
      setSelectedGroupId("");
    } catch {
      setGroupError("Không tải được danh sách nhóm.");
    } finally {
      setIsLoadingGroups(false);
    }
  };

  const handleAddGroup = (group: ApiConversation) => {
    const existingEmails = new Set(attendees.map((attendee) => attendee.email.toLowerCase()));
    const membersToAdd = group.members
      .filter((member) => member.email && !existingEmails.has(member.email.toLowerCase()))
      .map((member) => ({
        email: member.email.toLowerCase(),
        name: member.display_name || member.username || member.email,
        status: "needsAction" as const,
      }));

    if (membersToAdd.length === 0) {
      setGroupError("Nhóm này không có người tham gia mới.");
      return;
    }
    setAttendees((current) => [...current, ...membersToAdd]);
    setGroupError("");
    setSelectedGroupId("");
  };

  const updateReminder = (id: string, changes: Partial<EventReminder>) => {
    setReminders((current) => current.map((reminder) => (reminder.id === id ? { ...reminder, ...changes } : reminder)));
  };

  const addReminder = () => {
    const id = `reminder-${Date.now()}`;
    setReminders((current) => [...current, { id, method: "popup", minutes: 10 }]);
    setReminderUnits((current) => ({ ...current, [id]: "minutes" }));
  };

  const removeReminder = (id: string) => {
    setReminders((current) => current.filter((reminder) => reminder.id !== id));
    setReminderUnits((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;

    const dueAtStr = isAllDay ? buildSingleDayIso(date, "00:00") : buildSingleDayIso(date, startTime);
    const endAtStr = isAllDay ? buildSingleDayIso(date, "23:59") : buildSingleDayIso(date, endTime);

    const taskData: CalendarTask = {
      id: initialTask?.id ?? `task-${Date.now()}`,
      title: title.trim(),
      kind,
      date,
      isAllDay,
      startTime: isAllDay ? undefined : startTime,
      endTime: isAllDay ? undefined : endTime,
      duration: isAllDay ? 1440 : calculatedDuration,
      dueAt: dueAtStr,
      endAt: endAtStr,
      recurrence,
      colorId,
      location: kind === "event" && location.trim() ? location.trim() : undefined,
      attendees: kind === "event" && attendees.length > 0 ? attendees : undefined,
      reminders: reminders.length > 0 ? reminders : undefined,
      note: note.trim() || undefined,
      status: initialTask?.status ?? "approved",
      source: initialTask?.source ?? "manual",
      category: kind === "task" ? "task" : "meeting",
    };

    onSubmit(taskData);
  };

  return (
    <Modal
      title={initialTask ? (kind === "task" ? "Chỉnh sửa việc cần làm" : "Chỉnh sửa sự kiện") : (kind === "task" ? "Tạo việc cần làm mới" : "Tạo sự kiện mới")}
      maxWidth="max-w-xl"
      popoverAnchor={popoverAnchor}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit} className="calendar-event-form-scroll -mr-5 max-h-[calc(100dvh-8rem)] space-y-5 overflow-y-auto overscroll-contain pr-5">
        {/* The type is fixed while editing so the form only shows the selected item. */}
        {!initialTask && <div className="flex rounded-xl bg-[#F1F3F4] p-1 dark:bg-[#2D2E30]" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={kind === "event"}
            onClick={() => {
              setKind("event");
              if (colorId === "peacock") setColorId("sage");
            }}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium transition-all ${
              kind === "event"
                ? "bg-white text-[#1A73E8] shadow-xs dark:bg-[#444746] dark:text-[#A8C7FA]"
                : "text-[#5F6368] dark:text-[#C4C7C5]"
            }`}
          >
            <CalendarDays className="h-4 w-4" />
            <span>Sự kiện</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={kind === "task"}
            onClick={() => {
              setKind("task");
              if (colorId === "sage") setColorId("peacock");
            }}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium transition-all ${
              kind === "task"
                ? "bg-white text-[#1A73E8] shadow-xs dark:bg-[#444746] dark:text-[#A8C7FA]"
                : "text-[#5F6368] dark:text-[#C4C7C5]"
            }`}
          >
            <CheckCircle2 className="h-4 w-4" />
            <span>Việc cần làm</span>
          </button>
        </div>}

        {/* Tiêu đề */}
        <div>
          <input
            autoFocus
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={kind === "task" ? "Thêm tiêu đề việc cần làm" : "Thêm tiêu đề sự kiện"}
            className="w-full border-0 border-b border-[#DADCE0] bg-transparent px-0 py-2 text-xl font-normal text-[#202124] outline-none transition-colors placeholder:text-[#9AA0A6] focus:border-[#1A73E8] focus:ring-0 dark:border-[#5F6368] dark:text-[#E8EAED]"
          />
        </div>

        {/* ── THỜI GIAN: 1 NGÀY VỚI LỰA CHỌN CẢ NGÀY HOẶC THEO GIỜ ── */}
        <div className="space-y-4 rounded-2xl border border-[#DADCE0] bg-[#F8FAFD] p-4 dark:border-[#36373A] dark:bg-[#1E1F20]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Clock3 className="h-4 w-4 text-[#1A73E8]" />
              <span className="text-xs font-semibold text-[#1F1F1F] dark:text-[#E3E3E3]">
                Thời gian
              </span>
            </div>
            {/* Checkbox Cả ngày (Màu trắng khi chưa tích) */}
            <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">
              <CustomCheckbox checked={isAllDay} onChange={(checked) => { setIsAllDay(checked); setTimeChanged(true); }} />
              <span>Cả ngày</span>
            </label>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,0.9fr)_minmax(0,0.9fr)]">
            <div>
              <label className="block text-[11px] font-medium text-[#70757A] dark:text-[#9AA0A6] mb-1">
                Ngày diễn ra
              </label>
              <CalendarDateField value={date} onChange={(nextDate) => { setDate(nextDate); setTimeChanged(true); }} />
            </div>

            {!isAllDay && (
              <>
                <div>
                  <label className="block text-[11px] font-medium text-[#70757A] dark:text-[#9AA0A6] mb-1">
                    Bắt đầu
                  </label>
                  <CalendarTimeField
                    value={startTime}
                    onChange={handleStartTimeChange}
                    ariaLabel="Giờ bắt đầu"
                    className="w-full"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-[#70757A] dark:text-[#9AA0A6] mb-1">
                    Kết thúc
                  </label>
                  <CalendarTimeField
                    value={endTime}
                    onChange={handleEndTimeChange}
                    ariaLabel="Giờ kết thúc"
                    className="w-full"
                  />
                </div>
              </>
            )}
          </div>

          <div className="grid gap-1.5 border-t border-[#DADCE0]/70 pt-3 dark:border-[#36373A] sm:grid-cols-[3.5rem_auto] sm:items-center sm:justify-start">
            <label className="text-[11px] font-medium text-[#70757A] dark:text-[#9AA0A6]">
              Lặp lại
            </label>
            <select
              value={recurrence}
              onChange={(e) => setRecurrence(e.target.value as RecurrenceFreq)}
              className="w-full rounded-lg border border-[#DADCE0] bg-white px-3 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3] sm:w-auto"
            >
              <option value="none">Không lặp lại</option>
              <option value="daily">Hằng ngày</option>
              <option value="workdays">Thứ 2 đến Thứ 6</option>
              <option value="weekly">Hằng tuần vào ngày này</option>
              <option value="monthly">Hằng tháng vào ngày này</option>
              <option value="yearly">Hằng năm vào ngày này</option>
            </select>
          </div>
        </div>

        {/* ── NẾU LÀ SỰ KIỆN: ĐỊA ĐIỂM, KHÁCH MỜI ── */}
        {kind === "event" && (
          <>
            {/* Địa điểm */}
            <div>
              <label className="block text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5] mb-1">
                <span className="flex items-center gap-1"><MapPin className="h-3.5 w-3.5 text-[#1A73E8]" />Địa điểm</span>
              </label>
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Nhập địa điểm"
                className="w-full rounded-lg border border-[#DADCE0] bg-white px-3 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
              />
            </div>

            {/* Khách mời */}
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3">
                <label className="text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5]">
                  <span className="flex items-center gap-1"><Users className="h-3.5 w-3.5 text-[#1A73E8]" />Thêm người tham gia</span>
                </label>
              </div>
              {groupError && <p className="text-[11px] text-red-600 dark:text-red-300">{groupError}</p>}
              <div className="flex gap-2">
                <select
                  value={inviteMode}
                  onChange={(event) => void handleInviteModeChange(event.target.value as "person" | "group")}
                  aria-label="Loại người tham gia"
                  className="shrink-0 rounded-lg border border-[#DADCE0] bg-white px-2 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
                >
                  <option value="person">Thêm người</option>
                  <option value="group">Thêm nhóm</option>
                </select>
                {inviteMode === "person" ? (
                  <div className="relative min-w-0 flex-1">
                    <input
                      type="email"
                      value={guestEmail}
                      onChange={(e) => {
                        setGuestEmail(e.target.value);
                        if (guestError) setGuestError("");
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleAddGuest();
                        }
                      }}
                      placeholder="Nhập email người tham gia"
                      aria-invalid={Boolean(guestError)}
                      aria-describedby={guestError ? "attendee-email-error" : undefined}
                      className={`w-full rounded-lg border bg-white px-3 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:bg-[#2D2E30] dark:text-[#E3E3E3] ${guestError ? "border-red-500" : "border-[#DADCE0] dark:border-[#5F6368]"}`}
                    />
                    {(isSearchingGuests || suggestedUsers.length > 0) && (
                      <div className="absolute inset-x-0 top-[calc(100%+0.35rem)] z-20 overflow-hidden rounded-xl border border-[#DADCE0] bg-white p-1 shadow-lg dark:border-[#5F6368] dark:bg-[#2D2E30]">
                        {isSearchingGuests && <p className="px-2.5 py-2 text-[11px] text-[#70757A]">Đang tìm liên hệ…</p>}
                        {suggestedUsers.map((user) => (
                          <button
                            key={user.id}
                            type="button"
                            onClick={() => handleAddGuest(undefined, user.email)}
                            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
                          >
                            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[#E8F0FE] text-[10px] font-bold text-[#1A73E8] dark:bg-[#1A73E8]/20 dark:text-[#A8C7FA]">{(user.display_name || user.username || user.email).slice(0, 1).toUpperCase()}</span>
                            <span className="min-w-0"><span className="block truncate text-xs font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">{user.display_name || user.username}</span><span className="block truncate text-[11px] text-[#70757A]">{user.email}</span></span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <select
                    value={selectedGroupId}
                    onChange={(event) => setSelectedGroupId(event.target.value)}
                    disabled={isLoadingGroups}
                    className="min-w-0 flex-1 rounded-lg border border-[#DADCE0] bg-white px-3 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] disabled:cursor-wait dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
                  >
                    <option value="">{isLoadingGroups ? "Đang tải nhóm…" : "Chọn nhóm chat"}</option>
                    {groups.map((group) => <option key={group.id} value={group.id}>{group.title || "Nhóm chưa đặt tên"} · {group.members.length} người</option>)}
                  </select>
                )}
                <button
                  type="button"
                  onClick={() => {
                    if (inviteMode === "person") handleAddGuest();
                    else {
                      const group = groups.find((item) => item.id === selectedGroupId);
                      if (group) handleAddGroup(group);
                    }
                  }}
                  disabled={inviteMode === "person" ? !guestEmail.trim() : !selectedGroupId || isLoadingGroups}
                  className="shrink-0 rounded-lg bg-[#E8F0FE] px-3.5 py-2 text-xs font-medium text-[#1A73E8] hover:bg-[#D2E3FC] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-[#1A73E8]/20 dark:text-[#A8C7FA]"
                >
                  Thêm
                </button>
              </div>
              {guestError && <p id="attendee-email-error" className="text-[11px] text-red-600 dark:text-red-300">{guestError}</p>}

              {attendees.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {attendees.map((guest) => (
                    <span
                      key={guest.email}
                      className="inline-flex items-center gap-1.5 rounded-full bg-[#F1F3F4] px-2.5 py-1 text-xs text-[#3C4043] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
                    >
                      <span>{guest.email}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveGuest(guest.email)}
                        className="rounded-full hover:text-red-500"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {kind === "task" && (
          <div className="space-y-2">
            <label className="flex items-center gap-1 text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5]">
              <Clock3 className="h-3.5 w-3.5 text-[#1A73E8]" /> Thời hạn
            </label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[auto_auto] sm:justify-start">
              <CalendarDateField value={date} onChange={setDate} ariaLabel="Ngày thời hạn" containerClassName="w-48" />
              {!isAllDay && (
                <CalendarTimeField value={endTime} onChange={setEndTime} ariaLabel="Giờ thời hạn" />
              )}
            </div>
          </div>
        )}

        <div className="relative flex items-center gap-2">
          <span className="text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5]">Màu sắc</span>
          <button
            type="button"
            onClick={() => setShowColorPalette((current) => !current)}
            aria-label="Chọn màu sắc"
            aria-expanded={showColorPalette}
            className="flex h-8 items-center gap-1.5 rounded-lg border border-[#DADCE0] bg-white px-2 hover:bg-[#F8FAFD] dark:border-[#5F6368] dark:bg-[#2D2E30]"
          >
            <span className="h-4 w-4 rounded-full" style={{ backgroundColor: CALENDAR_PALETTE[colorId]?.bg }} />
            <ChevronDown className="h-3.5 w-3.5 text-[#70757A]" />
          </button>
          {showColorPalette && (
            <div className="absolute left-0 top-[calc(100%+0.5rem)] z-20 grid w-52 grid-cols-6 gap-2 rounded-xl border border-[#DADCE0] bg-white p-3 shadow-lg dark:border-[#5F6368] dark:bg-[#2D2E30]">
              {Object.values(CALENDAR_PALETTE).map((color) => (
                <button
                  key={color.id}
                  type="button"
                  title={color.name}
                  onClick={() => {
                    setColorId(color.id);
                    setShowColorPalette(false);
                  }}
                  className={`grid h-6 w-6 place-items-center rounded-full transition-transform hover:scale-110 ${colorId === color.id ? "ring-2 ring-[#1A73E8] ring-offset-2 dark:ring-offset-[#2D2E30]" : ""}`}
                  style={{ backgroundColor: color.bg }}
                >
                  {colorId === color.id && <Check className="h-3.5 w-3.5 text-white stroke-[3]" />}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ── THÔNG BÁO NHẮC LỊCH ── */}
        <div className="space-y-2">
          <span className="flex items-center gap-1 text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5]"><Bell className="h-3.5 w-3.5 text-[#1A73E8]" />Thông báo nhắc lịch</span>
          {reminders.map((reminder) => {
            const unit = reminderUnits[reminder.id] ?? "minutes";
            const divisor = unit === "days" ? 1440 : unit === "hours" ? 60 : 1;
            return (
              <div key={reminder.id} className="calendar-reminder-row flex items-center gap-2">
                <select
                  value={reminder.method}
                  onChange={(event) => updateReminder(reminder.id, { method: event.target.value as EventReminder["method"] })}
                  aria-label="Kênh nhắc lịch"
                  className="w-auto shrink-0 rounded-lg border border-[#DADCE0] bg-white px-2 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-white dark:text-[#1F1F1F]"
                >
                  <option value="popup">Thông báo</option>
                  <option value="email">Email</option>
                </select>
                <input
                  type="number"
                  min="0"
                  max="10080"
                  step="1"
                  value={Math.max(0, Math.round(reminder.minutes / divisor))}
                  onChange={(event) => updateReminder(reminder.id, { minutes: Math.max(0, Math.min(10080, (Number.parseInt(event.target.value, 10) || 0) * divisor)) })}
                  aria-label="Thời gian nhắc"
                  className="calendar-reminder-number w-14 shrink-0 rounded-lg border border-[#DADCE0] bg-white px-2 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-white dark:text-[#1F1F1F]"
                />
                <select
                  value={unit}
                  onChange={(event) => {
                    const nextUnit = event.target.value as ReminderUnit;
                    setReminderUnits((current) => ({ ...current, [reminder.id]: nextUnit }));
                    updateReminder(reminder.id, { minutes: Math.max(0, Math.min(10080, Math.round(reminder.minutes / divisor) * (nextUnit === "days" ? 1440 : nextUnit === "hours" ? 60 : 1))) });
                  }}
                  aria-label="Đơn vị thời gian nhắc"
                  className="w-auto shrink-0 rounded-lg border border-[#DADCE0] bg-white px-2 py-2 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-white dark:text-[#1F1F1F]"
                >
                  <option value="minutes">Phút</option>
                  <option value="hours">Giờ</option>
                  <option value="days">Ngày</option>
                </select>
                <button type="button" onClick={() => removeReminder(reminder.id)} aria-label="Xóa thông báo" className="rounded-lg p-2 text-[#70757A] hover:bg-[#F1F3F4] hover:text-red-600 dark:hover:bg-[#36373A]"><X className="h-4 w-4" /></button>
              </div>
            );
          })}
          <button type="button" onClick={addReminder} className="text-xs font-medium text-[#1A73E8] hover:underline dark:text-[#A8C7FA]">+ Thêm thông báo</button>
        </div>

        {/* ── GHI CHÚ / MÔ TẢ ── */}
        <div>
          <label className="block text-xs font-medium text-[#3C4043] dark:text-[#C4C7C5] mb-1">
            <span className="flex items-center gap-1"><FileText className="h-3.5 w-3.5 text-[#1A73E8]" />{kind === "task" ? "Chi tiết việc cần làm" : "Mô tả sự kiện"}</span>
          </label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder={kind === "task" ? "Thêm mô tả hoặc chi tiết việc cần thực hiện..." : "Thêm mô tả cho sự kiện..."}
            className="w-full rounded-xl border border-[#DADCE0] bg-white p-2.5 text-xs text-[#1F1F1F] outline-none focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
          />
        </div>

        {reviewWarning && !timeChanged && <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">{reviewWarning}</p>}
        {/* Footer Actions */}
        <div className="flex justify-end gap-2.5 pt-3 border-t border-[#DADCE0] dark:border-[#36373A]">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-5 py-2 text-xs font-medium text-[#70757A] transition-colors hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
          >
            Huỷ
          </button>
          <button
            type="submit"
            className="rounded-full bg-[#1A73E8] px-6 py-2 text-xs font-medium text-white transition-all hover:bg-[#1557B0] shadow-sm active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitLabel ?? (initialTask ? "Lưu thay đổi" : "Lưu")}
          </button>
        </div>
      </form>
    </Modal>
  );
};

/* ────────────────────────── EVENT DETAILS MODAL ────────────────────────── */
const EventDetailsModal: React.FC<{
  task: CalendarTask;
  anchor: DetailPopoverAnchor | null;
  onClose: () => void;
  onFinish: (task: CalendarTask) => void;
  onEdit: () => void;
  onDelete: () => void;
}> = ({ task, anchor, onClose, onFinish, onEdit, onDelete }) => {
  const color = getEventColor(task);
  const isTask = task.kind === "task";
  const taskDateObj = parseDateString(task.date || toDateString(new Date(task.dueAt)));

  return (
    <Modal title="" maxWidth="max-w-md" popoverAnchor={anchor} onClose={onClose}>
      <div className="space-y-4 max-h-[75vh] overflow-y-auto pr-1 scrollbar-thin">
        {/* Top Header Actions */}
        <div className="flex items-center justify-between border-b border-[#DADCE0] pb-2 dark:border-[#36373A]">
          <div className="flex items-center gap-1.5">
            <span
              className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
              style={{ backgroundColor: color.light, color: color.bg }}
            >
              {isTask ? "Việc cần làm" : "Sự kiện"}
            </span>
            {task.status === "done" && (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300">
                Đã hoàn thành
              </span>
            )}
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={onEdit}
              title="Chỉnh sửa"
              className="rounded-full p-1.5 text-[#444746] hover:bg-[#F1F3F4] dark:text-[#C4C7C5] dark:hover:bg-[#36373A]"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              onClick={onDelete}
              title="Xoá"
              className="rounded-full p-1.5 text-[#D93025] hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Title */}
        <div className="flex items-start gap-3">
          <span className="mt-1 h-4 w-4 shrink-0 rounded-md" style={{ backgroundColor: color.bg }} />
          <div className="min-w-0 flex-1">
            <h3 className={`text-lg font-medium text-[#1F1F1F] dark:text-[#E3E3E3] leading-snug ${task.status === "done" ? "line-through opacity-60" : ""}`}>
              {task.title}
            </h3>
            <p className="text-xs text-[#70757A] mt-0.5">
              {formatVietnameseDate(taskDateObj, true)}
            </p>
          </div>
        </div>

        {/* Single-Day Time Card */}
        <div className="rounded-2xl border border-[#DADCE0] bg-[#F8FAFD] p-3 text-xs space-y-1.5 dark:border-[#36373A] dark:bg-[#1E1F20]">
          <div className="flex items-center gap-2 font-medium text-[#1F1F1F] dark:text-[#E3E3E3]">
            <Clock3 className="h-4 w-4 text-[#1A73E8] shrink-0" />
            <span>
              {isAllDayTask(task)
                ? "Sự kiện cả ngày"
                : `${task.startTime || toTimeString(new Date(task.dueAt))} – ${task.endTime || (task.endAt ? toTimeString(new Date(task.endAt)) : "")} (${formatMinutesDuration(task.duration)})`}
            </span>
          </div>
          {task.recurrence && task.recurrence !== "none" && (
            <div className="flex items-center gap-1 text-[11px] text-[#1A73E8] font-medium pl-6 dark:text-[#A8C7FA]">
              <RotateCw className="h-3 w-3" />
              {recurrenceLabel(task.recurrence)}
            </div>
          )}
        </div>

        {/* Location */}
        {task.location && (
          <div className="flex items-start gap-2.5 text-xs text-[#3C4043] dark:text-[#C4C7C5]">
            <MapPin className="h-4 w-4 text-[#1A73E8] shrink-0 mt-0.5" />
            <span className="font-medium">{task.location}</span>
          </div>
        )}

        {/* Attendees */}
        {task.attendees && task.attendees.length > 0 && (
          <div className="rounded-2xl border border-[#DADCE0] p-3 text-xs space-y-1.5 dark:border-[#36373A]">
            <span className="font-semibold text-[#1F1F1F] dark:text-[#E3E3E3] flex items-center gap-1.5">
              <Users className="h-3.5 w-3.5 text-[#1A73E8]" />
              Người tham gia ({task.attendees.length})
            </span>
            <div className="flex flex-wrap gap-1 pt-1">
              {task.attendees.map((g) => (
                <span key={g.email} className="rounded-full bg-[#F1F3F4] px-2.5 py-0.5 text-[11px] text-[#3C4043] dark:bg-[#2D2E30] dark:text-[#E3E3E3]">
                  {g.email}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Description */}
        {task.note && (
          <div className="rounded-2xl bg-[#F1F3F4] p-3 text-xs leading-relaxed text-[#3C4043] dark:bg-[#2D2E30] dark:text-[#C4C7C5]">
            <span className="block text-[10px] font-semibold uppercase tracking-wider text-[#70757A] mb-1">Mô tả</span>
            {task.note}
          </div>
        )}

        {/* Bottom Actions */}
        <div className="flex items-center justify-between border-t border-[#DADCE0] pt-3 dark:border-[#36373A]">
          <button
            onClick={() => {
              onFinish(task);
              onClose();
            }}
            className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-xs font-medium transition-colors ${
              task.status === "done"
                ? "border border-gray-300 text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300"
                : "border border-emerald-600 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-400 dark:text-emerald-300"
            }`}
          >
            <Check className="h-3.5 w-3.5" />
            {task.status === "done" ? "Đánh dấu chưa xong" : "Đánh dấu hoàn thành"}
          </button>
        </div>
      </div>
    </Modal>
  );
};

/* ────────────────────────── CALENDAR DATE FIELD ────────────────────────── */
const CalendarDateField: React.FC<{
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  containerClassName?: string;
}> = ({ value, onChange, ariaLabel = "Ngày diễn ra", containerClassName = "" }) => {
  const selected = new Date(`${value}T00:00:00`);
  const [open, setOpen] = useState(false);
  const [visibleMonth, setVisibleMonth] = useState(() => new Date(selected.getFullYear(), selected.getMonth(), 1));

  useEffect(() => {
    if (!open) {
      const nextSelected = new Date(`${value}T00:00:00`);
      setVisibleMonth(new Date(nextSelected.getFullYear(), nextSelected.getMonth(), 1));
    }
  }, [open, value]);

  const monthStart = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth(), 1);
  const gridStart = new Date(monthStart);
  gridStart.setDate(1 - ((monthStart.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, index) => {
    const day = new Date(gridStart);
    day.setDate(gridStart.getDate() + index);
    return day;
  });
  const selectedKey = toDateString(selected);
  const todayKey = toDateString(new Date());

  return (
    <div className={`relative ${containerClassName}`}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label={ariaLabel}
        aria-expanded={open}
        className="flex w-full items-center justify-between rounded-lg border border-[#DADCE0] bg-white px-3 py-2 text-left text-xs font-medium text-[#1F1F1F] outline-none hover:border-[#1A73E8] focus:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]"
      >
        <span>{selected.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" })}</span>
        <CalendarDays className="h-3.5 w-3.5 text-[#1A73E8]" />
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+0.4rem)] z-30 w-72 rounded-xl border border-[#DADCE0] bg-white p-3 shadow-xl dark:border-[#5F6368] dark:bg-[#2D2E30]">
          <div className="mb-2 flex items-center justify-between">
            <button type="button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1))} className="rounded-full p-1 hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]" aria-label="Tháng trước"><ChevronLeft className="h-4 w-4" /></button>
            <span className="text-xs font-semibold text-[#1F1F1F] dark:text-[#E3E3E3]">{visibleMonth.toLocaleDateString("vi-VN", { month: "long", year: "numeric" })}</span>
            <button type="button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1))} className="rounded-full p-1 hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]" aria-label="Tháng sau"><ChevronRight className="h-4 w-4" /></button>
          </div>
          <div className="grid grid-cols-7 text-center text-[10px] font-medium text-[#70757A]">
            {["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map((day) => <span key={day} className="py-1">{day}</span>)}
          </div>
          <div className="grid grid-cols-7 gap-y-0.5">
            {days.map((day) => {
              const dayKey = toDateString(day);
              const isCurrentMonth = day.getMonth() === visibleMonth.getMonth();
              const isSelected = dayKey === selectedKey;
              return <button key={dayKey} type="button" onClick={() => { onChange(dayKey); setOpen(false); }} className={`mx-auto grid h-8 w-8 place-items-center rounded-full text-xs transition-colors ${isSelected ? "bg-[#1A73E8] font-bold text-white" : dayKey === todayKey ? "font-bold text-[#1A73E8] hover:bg-[#E8F0FE]" : isCurrentMonth ? "text-[#1F1F1F] hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#36373A]" : "text-[#9AA0A6] hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"}`}>{day.getDate()}</button>;
            })}
          </div>
        </div>
      )}
    </div>
  );
};

const CalendarTimeField: React.FC<{
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  className?: string;
}> = ({ value, onChange, ariaLabel = "Giờ", className = "" }) => {
  const [open, setOpen] = useState(false);
  const [showValidationError, setShowValidationError] = useState(false);
  const optionsRef = useRef<HTMLDivElement | null>(null);
  const optionsId = useId();
  const formatAmPm = (time: string) => {
    const [hourPart = "00", minutePart = "00"] = time.split(":");
    const hour = Number(hourPart);
    const period = hour >= 12 ? "PM" : "AM";
    const displayHour = hour % 12 || 12;
    return `${displayHour}:${minutePart} ${period}`;
  };
  const [draft, setDraft] = useState(formatAmPm(value));
  const parseAmPm = (time: string) => {
    const match = time.trim().match(/^(\d{1,2}):([0-5]\d)\s*(AM|PM)$/i);
    if (!match) return null;
    const inputHour = Number(match[1]);
    const minute = Number(match[2]);
    if (inputHour < 1 || inputHour > 12) return null;
    const hour = (inputHour % 12) + (match[3].toUpperCase() === "PM" ? 12 : 0);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  };
  const isValidTime = parseAmPm(draft) !== null;
  const timeOptions = Array.from({ length: 96 }, (_, index) => {
    const minutes = index * 15;
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  });

  useEffect(() => {
    if (!open) {
      setDraft(formatAmPm(value));
      setShowValidationError(false);
    }
  }, [open, value]);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      optionsRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, value]);

  return (
    <div className={`relative ${className}`}>
      <div className="flex w-full items-center gap-2 rounded-lg border border-[#DADCE0] bg-white px-3 py-2 text-xs text-[#1F1F1F] transition-colors focus-within:border-[#1A73E8] dark:border-[#5F6368] dark:bg-[#2D2E30] dark:text-[#E3E3E3]">
        <Clock3 className="h-3.5 w-3.5 shrink-0 text-[#1A73E8]" />
        <input
          type="text"
          value={draft}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onChange={(event) => {
            const sanitized = event.target.value.toUpperCase().replace(/[^0-9:\sAPM]/g, "");
            setDraft(sanitized);
            setShowValidationError(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && isValidTime) {
              event.preventDefault();
              const parsed = parseAmPm(draft);
              if (parsed) onChange(parsed);
              setOpen(false);
            }
            if (event.key === "Enter" && !isValidTime) {
              event.preventDefault();
              setShowValidationError(true);
            }
            if (event.key === "Escape") setOpen(false);
          }}
          onBlur={() => {
            const parsed = parseAmPm(draft);
            if (parsed) {
              onChange(parsed);
              setShowValidationError(false);
            } else {
              setShowValidationError(true);
            }
          }}
          placeholder="9:00 AM"
          role="combobox"
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-controls={optionsId}
          aria-haspopup="listbox"
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-[#9AA0A6]"
        />
      </div>
      {open && (
        <div className="absolute left-0 top-[calc(100%+0.4rem)] z-30 w-full max-w-full rounded-xl border border-[#DADCE0] bg-white p-2 shadow-xl dark:border-[#5F6368] dark:bg-[#2D2E30]">
          {showValidationError && (
            <p className="px-2 pb-2 text-[11px] text-red-600 dark:text-red-300">Thời gian không hợp lệ.</p>
          )}
          <div id={optionsId} ref={optionsRef} className="max-h-56 overflow-y-auto py-1" role="listbox" aria-label={`${ariaLabel}: các mốc 15 phút`}>
            {timeOptions.map((time) => (
              <button
                key={time}
                type="button"
                role="option"
                aria-selected={value === time}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => { onChange(time); setOpen(false); }}
                className={`flex w-full rounded-lg px-3 py-1.5 text-left text-xs transition-colors ${value === time ? "bg-[#E8F0FE] font-semibold text-[#1A73E8] dark:bg-[#1A73E8]/20 dark:text-[#A8C7FA]" : "text-[#3C4043] hover:bg-[#F1F3F4] dark:text-[#E3E3E3] dark:hover:bg-[#36373A]"}`}
              >
                {formatAmPm(time)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

/* ────────────────────────── SHARED MODAL SHELL ────────────────────────── */
const Modal: React.FC<{
  title?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
  popoverAnchor?: DetailPopoverAnchor | null;
}> = ({ title, onClose, children, maxWidth = "max-w-md", popoverAnchor = null }) => {
  const isDetailsPopover = typeof title === "string" && title.startsWith("Chi tiết");
  const isAnchoredPopover = Boolean(popoverAnchor);
  const viewportWidth = typeof window === "undefined" ? 1440 : window.innerWidth;
  const viewportHeight = typeof window === "undefined" ? 900 : window.innerHeight;
  const canOpenRight = popoverAnchor && viewportWidth - popoverAnchor.right >= 420;

  const anchoredStyle = popoverAnchor
    ? {
        left: Math.max(16, canOpenRight ? popoverAnchor.right + 12 : popoverAnchor.left - 410),
        top: Math.max(72, Math.min(popoverAnchor.top - 12, viewportHeight - 560)),
      }
    : undefined;

  return (
    <div
      className={`fixed inset-0 z-50 flex p-3 ${
        isAnchoredPopover
          ? "pointer-events-none items-start justify-end bg-transparent pt-20 sm:pr-8"
          : "items-center justify-center bg-black/40 backdrop-blur-[2px]"
      }`}
      role="dialog"
      aria-modal="true"
    >
      <div
        style={anchoredStyle}
        className={`pointer-events-auto w-full max-h-[calc(100dvh-1.5rem)] overflow-hidden ${
          isDetailsPopover
            ? `${popoverAnchor ? "fixed" : ""} max-w-sm max-h-[calc(100vh-6rem)] overflow-y-auto`
            : isAnchoredPopover
              ? "fixed max-w-md overflow-y-auto"
              : maxWidth
        } animate-[fadeIn_150ms_ease-out] rounded-3xl bg-white p-5 shadow-[0_24px_38px_3px_rgba(0,0,0,.14),0_9px_46px_8px_rgba(0,0,0,.12)] dark:bg-[#2D2E30]`}
      >
        {title ? (
          <div className="mb-3.5 flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#1F1F1F] dark:text-[#E3E3E3]">{title}</h2>
            <button
              onClick={onClose}
              aria-label="Đóng"
              className="rounded-full p-1.5 text-[#70757A] hover:bg-[#F1F3F4] dark:hover:bg-[#36373A]"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
};
