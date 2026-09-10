import type { LanguageCode } from "@/shared/types/language";

type Key = "chats" | "all" | "unread" | "groups" | "search" | "new" | "settings" | "language" | "profile" | "notifications" | "privacy" | "aiTools" | "markRead" | "mute" | "noChats" | "adjustSearch";
const en: Record<Key, string> = { chats: "Chats", all: "All", unread: "Unread", groups: "Groups", search: "Search chats...", new: "new", settings: "Settings", language: "Language & translation", profile: "Profile", notifications: "Notifications", privacy: "Privacy & security", aiTools: "Personal assistant chatbot", markRead: "Mark all as read", mute: "Mute notifications", noChats: "No chats found", adjustSearch: "Try a different search or filter" };
const vi: Record<Key, string> = { chats: "Trò chuyện", all: "Tất cả", unread: "Chưa đọc", groups: "Nhóm", search: "Tìm kiếm cuộc trò chuyện...", new: "mới", settings: "Cài đặt", language: "Ngôn ngữ & Bản dịch", profile: "Hồ sơ", notifications: "Thông báo", privacy: "Quyền riêng tư & Bảo mật", aiTools: "Chatbot trợ lý cá nhân", markRead: "Đánh dấu tất cả đã đọc", mute: "Tắt thông báo", noChats: "Không tìm thấy cuộc trò chuyện", adjustSearch: "Hãy thử thay đổi từ khóa hoặc bộ lọc" };
const labels: Record<LanguageCode, Record<Key, string>> = {
  en, vi,
  ja: { chats:"チャット",all:"すべて",unread:"未読",groups:"グループ",search:"会話を検索...",new:"新着",settings:"設定",language:"言語と翻訳",profile:"プロフィール",notifications:"通知",privacy:"プライバシーとセキュリティ",aiTools:"パーソナルアシスタント",markRead:"すべて既読にする",mute:"通知をミュート",noChats:"会話が見つかりません",adjustSearch:"検索またはフィルターを変更してください" },
  ko: { chats:"채팅",all:"전체",unread:"읽지 않음",groups:"그룹",search:"대화 검색...",new:"새 항목",settings:"설정",language:"언어 및 번역",profile:"프로필",notifications:"알림",privacy:"개인정보 및 보안",aiTools:"개인 비서",markRead:"모두 읽음으로 표시",mute:"알림 음소거",noChats:"대화를 찾을 수 없음",adjustSearch:"검색 또는 필터를 조정해 보세요" },
  zh: { chats:"聊天",all:"全部",unread:"未读",groups:"群组",search:"搜索对话...",new:"新消息",settings:"设置",language:"语言与翻译",profile:"个人资料",notifications:"通知",privacy:"隐私与安全",aiTools:"个人助理",markRead:"全部标为已读",mute:"静音通知",noChats:"未找到对话",adjustSearch:"请调整搜索或筛选条件" },
  es: { chats:"Chats",all:"Todos",unread:"No leídos",groups:"Grupos",search:"Buscar conversaciones...",new:"nuevo",settings:"Configuración",language:"Idioma y traducción",profile:"Perfil",notifications:"Notificaciones",privacy:"Privacidad y seguridad",aiTools:"Asistente personal",markRead:"Marcar todo como leído",mute:"Silenciar notificaciones",noChats:"No se encontraron conversaciones",adjustSearch:"Ajusta la búsqueda o el filtro" },
  fr: { chats:"Discussions",all:"Tout",unread:"Non lus",groups:"Groupes",search:"Rechercher des conversations...",new:"nouveau",settings:"Paramètres",language:"Langue et traduction",profile:"Profil",notifications:"Notifications",privacy:"Confidentialité et sécurité",aiTools:"Assistant personnel",markRead:"Tout marquer comme lu",mute:"Désactiver les notifications",noChats:"Aucune discussion trouvée",adjustSearch:"Modifiez la recherche ou le filtre" },
  de: { chats:"Chats",all:"Alle",unread:"Ungelesen",groups:"Gruppen",search:"Unterhaltungen suchen...",new:"neu",settings:"Einstellungen",language:"Sprache und Übersetzung",profile:"Profil",notifications:"Benachrichtigungen",privacy:"Datenschutz und Sicherheit",aiTools:"Persönlicher Assistent",markRead:"Alle als gelesen markieren",mute:"Benachrichtigungen stummschalten",noChats:"Keine Unterhaltungen gefunden",adjustSearch:"Suche oder Filter anpassen" },
  th: { chats:"แชต",all:"ทั้งหมด",unread:"ยังไม่อ่าน",groups:"กลุ่ม",search:"ค้นหาการสนทนา...",new:"ใหม่",settings:"การตั้งค่า",language:"ภาษาและการแปล",profile:"โปรไฟล์",notifications:"การแจ้งเตือน",privacy:"ความเป็นส่วนตัวและความปลอดภัย",aiTools:"ผู้ช่วยส่วนตัว",markRead:"ทำเครื่องหมายว่าอ่านแล้วทั้งหมด",mute:"ปิดเสียงการแจ้งเตือน",noChats:"ไม่พบการสนทนา",adjustSearch:"ลองปรับการค้นหาหรือตัวกรอง" },
  id: { chats:"Chat",all:"Semua",unread:"Belum dibaca",groups:"Grup",search:"Cari percakapan...",new:"baru",settings:"Pengaturan",language:"Bahasa & Terjemahan",profile:"Profil",notifications:"Notifikasi",privacy:"Privasi & Keamanan",aiTools:"Asisten pribadi",markRead:"Tandai semua sudah dibaca",mute:"Bisukan notifikasi",noChats:"Percakapan tidak ditemukan",adjustSearch:"Coba sesuaikan pencarian atau filter" },
  pt: { chats:"Conversas",all:"Todas",unread:"Não lidas",groups:"Grupos",search:"Pesquisar conversas...",new:"nova",settings:"Configurações",language:"Idioma e tradução",profile:"Perfil",notifications:"Notificações",privacy:"Privacidade e segurança",aiTools:"Assistente pessoal",markRead:"Marcar todas como lidas",mute:"Silenciar notificações",noChats:"Nenhuma conversa encontrada",adjustSearch:"Tente ajustar a pesquisa ou o filtro" },
  ru: { chats:"Чаты",all:"Все",unread:"Непрочитанные",groups:"Группы",search:"Поиск чатов...",new:"новое",settings:"Настройки",language:"Язык и перевод",profile:"Профиль",notifications:"Уведомления",privacy:"Конфиденциальность и безопасность",aiTools:"Персональный помощник",markRead:"Отметить всё как прочитанное",mute:"Отключить уведомления",noChats:"Чаты не найдены",adjustSearch:"Измените поиск или фильтр" },
  ar: { chats:"الدردشات",all:"الكل",unread:"غير المقروءة",groups:"المجموعات",search:"ابحث في الدردشات...",new:"جديد",settings:"الإعدادات",language:"اللغة والترجمة",profile:"الملف الشخصي",notifications:"الإشعارات",privacy:"الخصوصية والأمان",aiTools:"المساعد الشخصي",markRead:"وضع علامة مقروءة للكل",mute:"كتم الإشعارات",noChats:"لم يتم العثور على محادثات",adjustSearch:"جرّب تغيير البحث أو الفلتر" },
  hi: { chats:"चैट",all:"सभी",unread:"अपठित",groups:"समूह",search:"चैट खोजें...",new:"नया",settings:"सेटिंग्स",language:"भाषा और अनुवाद",profile:"प्रोफ़ाइल",notifications:"सूचनाएँ",privacy:"गोपनीयता और सुरक्षा",aiTools:"निजी सहायक",markRead:"सभी को पढ़ा हुआ चिह्नित करें",mute:"सूचनाएँ म्यूट करें",noChats:"कोई चैट नहीं मिली",adjustSearch:"खोज या फ़िल्टर बदलकर देखें" },
};
export const shellText = (language: LanguageCode, key: Key) => labels[language][key];

export type ShellChromeKey =
  | "sidebarNavigation"
  | "mainMenu"
  | "home"
  | "personalCalendar"
  | "taskInbox"
  | "ttsUnavailable";

const chrome: Record<LanguageCode, Record<ShellChromeKey, string>> = {
  en: { sidebarNavigation:"Sidebar navigation",mainMenu:"Main menu",home:"LinguaFlow home",personalCalendar:"Personal calendar",taskInbox:"Task inbox",ttsUnavailable:"Text-to-speech unavailable" },
  vi: { sidebarNavigation:"Điều hướng thanh bên",mainMenu:"Trình đơn chính",home:"Trang chủ LinguaFlow",personalCalendar:"Lịch cá nhân",taskInbox:"Hộp thư công việc",ttsUnavailable:"Không thể chuyển văn bản thành giọng nói" },
  ja: { sidebarNavigation:"サイドバーのナビゲーション",mainMenu:"メインメニュー",home:"LinguaFlow ホーム",personalCalendar:"個人カレンダー",taskInbox:"タスク受信箱",ttsUnavailable:"音声読み上げを利用できません" },
  ko: { sidebarNavigation:"사이드바 탐색",mainMenu:"주 메뉴",home:"LinguaFlow 홈",personalCalendar:"개인 캘린더",taskInbox:"작업함",ttsUnavailable:"텍스트 음성 변환을 사용할 수 없습니다" },
  zh: { sidebarNavigation:"侧边栏导航",mainMenu:"主菜单",home:"LinguaFlow 首页",personalCalendar:"个人日历",taskInbox:"任务收件箱",ttsUnavailable:"文字转语音不可用" },
  es: { sidebarNavigation:"Navegación lateral",mainMenu:"Menú principal",home:"Inicio de LinguaFlow",personalCalendar:"Calendario personal",taskInbox:"Bandeja de tareas",ttsUnavailable:"La conversión de texto a voz no está disponible" },
  fr: { sidebarNavigation:"Navigation latérale",mainMenu:"Menu principal",home:"Accueil LinguaFlow",personalCalendar:"Calendrier personnel",taskInbox:"Boîte de tâches",ttsUnavailable:"La synthèse vocale est indisponible" },
  de: { sidebarNavigation:"Seitennavigation",mainMenu:"Hauptmenü",home:"LinguaFlow-Startseite",personalCalendar:"Persönlicher Kalender",taskInbox:"Aufgabeneingang",ttsUnavailable:"Sprachausgabe ist nicht verfügbar" },
  th: { sidebarNavigation:"การนำทางแถบด้านข้าง",mainMenu:"เมนูหลัก",home:"หน้าแรก LinguaFlow",personalCalendar:"ปฏิทินส่วนตัว",taskInbox:"กล่องงาน",ttsUnavailable:"ไม่สามารถใช้การแปลงข้อความเป็นเสียงได้" },
  id: { sidebarNavigation:"Navigasi bilah samping",mainMenu:"Menu utama",home:"Beranda LinguaFlow",personalCalendar:"Kalender pribadi",taskInbox:"Kotak masuk tugas",ttsUnavailable:"Teks ke ucapan tidak tersedia" },
  pt: { sidebarNavigation:"Navegação lateral",mainMenu:"Menu principal",home:"Início do LinguaFlow",personalCalendar:"Calendário pessoal",taskInbox:"Caixa de tarefas",ttsUnavailable:"A conversão de texto em voz não está disponível" },
  ru: { sidebarNavigation:"Боковая навигация",mainMenu:"Главное меню",home:"Главная LinguaFlow",personalCalendar:"Личный календарь",taskInbox:"Входящие задачи",ttsUnavailable:"Озвучивание текста недоступно" },
  ar: { sidebarNavigation:"التنقل الجانبي",mainMenu:"القائمة الرئيسية",home:"صفحة LinguaFlow الرئيسية",personalCalendar:"التقويم الشخصي",taskInbox:"صندوق المهام",ttsUnavailable:"تحويل النص إلى كلام غير متاح" },
  hi: { sidebarNavigation:"साइडबार नेविगेशन",mainMenu:"मुख्य मेनू",home:"LinguaFlow होम",personalCalendar:"व्यक्तिगत कैलेंडर",taskInbox:"कार्य इनबॉक्स",ttsUnavailable:"टेक्स्ट-टू-स्पीच उपलब्ध नहीं है" },
};

export const shellChromeText = (language: LanguageCode, key: ShellChromeKey) => chrome[language][key];

export interface ErrorBoundaryCopy {
  eyebrow: string;
  title: string;
  body: string;
  action: string;
}
const routeError: Record<LanguageCode, ErrorBoundaryCopy> = {
  en: { eyebrow:"Connection interrupted",title:"LinguaFlow could not load this screen",body:"Your data is still safe. Check your connection and try this screen again.",action:"Try again" },
  vi: { eyebrow:"Kết nối bị gián đoạn",title:"LinguaFlow không thể tải màn hình này",body:"Dữ liệu của bạn vẫn an toàn. Hãy kiểm tra kết nối và thử lại.",action:"Thử lại" },
  ja: { eyebrow:"接続が中断されました",title:"この画面を読み込めませんでした",body:"データは安全です。接続を確認してもう一度お試しください。",action:"再試行" },
  ko: { eyebrow:"연결이 끊겼습니다",title:"이 화면을 불러올 수 없습니다",body:"데이터는 안전합니다. 연결을 확인하고 다시 시도하세요.",action:"다시 시도" },
  zh: { eyebrow:"连接已中断",title:"无法加载此页面",body:"你的数据仍然安全。请检查连接后重试。",action:"重试" },
  es: { eyebrow:"Conexión interrumpida",title:"LinguaFlow no pudo cargar esta pantalla",body:"Tus datos siguen seguros. Comprueba la conexión e inténtalo de nuevo.",action:"Reintentar" },
  fr: { eyebrow:"Connexion interrompue",title:"LinguaFlow n’a pas pu charger cet écran",body:"Vos données restent en sécurité. Vérifiez la connexion et réessayez.",action:"Réessayer" },
  de: { eyebrow:"Verbindung unterbrochen",title:"LinguaFlow konnte diese Ansicht nicht laden",body:"Deine Daten sind sicher. Prüfe die Verbindung und versuche es erneut.",action:"Erneut versuchen" },
  th: { eyebrow:"การเชื่อมต่อขาดหาย",title:"LinguaFlow ไม่สามารถโหลดหน้าจอนี้ได้",body:"ข้อมูลของคุณยังปลอดภัย โปรดตรวจสอบการเชื่อมต่อแล้วลองอีกครั้ง",action:"ลองอีกครั้ง" },
  id: { eyebrow:"Koneksi terputus",title:"LinguaFlow tidak dapat memuat layar ini",body:"Data Anda tetap aman. Periksa koneksi lalu coba lagi.",action:"Coba lagi" },
  pt: { eyebrow:"Conexão interrompida",title:"O LinguaFlow não carregou esta tela",body:"Seus dados continuam seguros. Verifique a conexão e tente novamente.",action:"Tentar novamente" },
  ru: { eyebrow:"Соединение прервано",title:"LinguaFlow не удалось загрузить этот экран",body:"Ваши данные в безопасности. Проверьте соединение и повторите попытку.",action:"Повторить" },
  ar: { eyebrow:"انقطع الاتصال",title:"تعذّر على LinguaFlow تحميل هذه الشاشة",body:"بياناتك ما زالت آمنة. تحقق من الاتصال وحاول مرة أخرى.",action:"إعادة المحاولة" },
  hi: { eyebrow:"कनेक्शन बाधित हुआ",title:"LinguaFlow यह स्क्रीन लोड नहीं कर सका",body:"आपका डेटा सुरक्षित है। कनेक्शन जाँचें और फिर प्रयास करें।",action:"फिर प्रयास करें" },
};

const globalError: Record<LanguageCode, ErrorBoundaryCopy> = Object.fromEntries(
  (Object.keys(routeError) as LanguageCode[]).map((language) => [language, {
    ...routeError[language],
    eyebrow: language === "vi" ? "Lỗi ứng dụng không mong muốn" : routeError[language].eyebrow,
    title: language === "vi" ? "LinguaFlow cần khôi phục" : routeError[language].title,
    body: language === "vi" ? "Hãy tải lại ứng dụng. Văn bản chưa lưu có thể cần nhập lại." : routeError[language].body,
    action: language === "vi" ? "Tải lại ứng dụng" : routeError[language].action,
  }]),
) as Record<LanguageCode, ErrorBoundaryCopy>;

export const errorBoundaryText = (language: LanguageCode, scope: "route" | "global") =>
  (scope === "route" ? routeError : globalError)[language];

export function detectInterfaceLanguage(): LanguageCode {
  if (typeof document !== "undefined" && document.documentElement.lang) {
    const documentLanguage = document.documentElement.lang.split("-")[0] as LanguageCode;
    if (documentLanguage in labels) return documentLanguage;
  }
  if (typeof navigator !== "undefined") {
    const browserLanguage = navigator.language.split("-")[0] as LanguageCode;
    if (browserLanguage in labels) return browserLanguage;
  }
  return "en";
}
