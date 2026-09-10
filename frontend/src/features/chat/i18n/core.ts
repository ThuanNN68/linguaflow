import type { LanguageCode } from "../types";

const emptyChat: Record<LanguageCode, [string, string, string]> = {
  en: ["Your conversations live here", "Chat naturally across languages with AI-assisted instant translation and real-time smart suggestions.", "Start a conversation"],
  vi: ["Các cuộc trò chuyện của bạn ở đây", "Trò chuyện xuyên ngôn ngữ với bản dịch tức thời và gợi ý thông minh theo thời gian thực.", "Bắt đầu cuộc trò chuyện"],
  ja: ["会話はここに表示されます", "AIによる即時翻訳とリアルタイムの提案で、言語を越えて自然に会話できます。", "会話を始める"],
  ko: ["대화가 여기에 표시됩니다", "AI 기반 즉시 번역과 실시간 제안으로 언어의 장벽 없이 대화하세요.", "대화 시작"],
  zh: ["你的对话会显示在这里", "借助 AI 即时翻译和实时智能建议，轻松跨语言交流。", "开始对话"],
  es: ["Tus conversaciones aparecen aquí", "Chatea entre idiomas con traducción instantánea y sugerencias inteligentes en tiempo real.", "Iniciar una conversación"],
  fr: ["Vos conversations apparaissent ici", "Discutez dans toutes les langues avec traduction instantanée et suggestions intelligentes en temps réel.", "Démarrer une conversation"],
  de: ["Deine Unterhaltungen erscheinen hier", "Unterhalte dich sprachübergreifend mit Sofortübersetzung und intelligenten Vorschlägen in Echtzeit.", "Unterhaltung starten"],
  th: ["การสนทนาของคุณจะแสดงที่นี่", "สนทนาข้ามภาษาได้อย่างเป็นธรรมชาติด้วยการแปลทันทีและคำแนะนำอัจฉริยะแบบเรียลไทม์", "เริ่มการสนทนา"],
  id: ["Percakapan Anda ada di sini", "Mengobrol lintas bahasa dengan terjemahan instan dan saran cerdas waktu nyata.", "Mulai percakapan"],
  pt: ["Suas conversas ficam aqui", "Converse entre idiomas com tradução instantânea e sugestões inteligentes em tempo real.", "Iniciar uma conversa"],
  ru: ["Здесь будут ваши беседы", "Общайтесь на разных языках с мгновенным переводом и умными подсказками.", "Начать беседу"],
  ar: ["محادثاتك هنا", "تواصل عبر اللغات بترجمة فورية واقتراحات ذكية في الوقت الفعلي.", "بدء محادثة"],
  hi: ["आपकी बातचीत यहाँ रहेगी", "तुरंत अनुवाद और स्मार्ट सुझावों के साथ भाषाओं के पार सहजता से चैट करें।", "बातचीत शुरू करें"],
};

export const emptyChatText = (language: LanguageCode) => emptyChat[language];

type CallCopy = {
  calling: string;
  incomingVoice: string;
  incomingVideo: string;
  cancel: string;
  answer: string;
  decline: string;
  end: string;
  microphoneOn: string;
  microphoneOff: string;
  cameraOn: string;
  cameraOff: string;
  connectionError: string;
};

const callCopy: Record<LanguageCode, CallCopy> = {
  en: { calling: "Calling…", incomingVoice: "Incoming voice call", incomingVideo: "Incoming video call", cancel: "Cancel call", answer: "Answer call", decline: "Decline call", end: "End call", microphoneOn: "Turn on microphone", microphoneOff: "Mute microphone", cameraOn: "Turn on camera", cameraOff: "Turn off camera", connectionError: "Could not connect to the call. Check microphone/camera permission and try again." },
  vi: { calling: "Đang gọi…", incomingVoice: "Cuộc gọi thoại đến", incomingVideo: "Cuộc gọi video đến", cancel: "Hủy cuộc gọi", answer: "Nghe cuộc gọi", decline: "Từ chối cuộc gọi", end: "Kết thúc cuộc gọi", microphoneOn: "Bật micro", microphoneOff: "Tắt micro", cameraOn: "Bật camera", cameraOff: "Tắt camera", connectionError: "Không thể kết nối cuộc gọi. Hãy kiểm tra quyền micro/camera và thử lại." },
  ja: { calling: "発信中…", incomingVoice: "音声通話の着信", incomingVideo: "ビデオ通話の着信", cancel: "通話をキャンセル", answer: "応答する", decline: "拒否する", end: "通話を終了", microphoneOn: "マイクをオン", microphoneOff: "マイクをミュート", cameraOn: "カメラをオン", cameraOff: "カメラをオフ", connectionError: "通話に接続できません。マイクとカメラの権限を確認して、もう一度お試しください。" },
  ko: { calling: "통화 중…", incomingVoice: "음성 통화 수신", incomingVideo: "영상 통화 수신", cancel: "통화 취소", answer: "통화 받기", decline: "거절", end: "통화 종료", microphoneOn: "마이크 켜기", microphoneOff: "마이크 음소거", cameraOn: "카메라 켜기", cameraOff: "카메라 끄기", connectionError: "통화에 연결할 수 없습니다. 마이크/카메라 권한을 확인하고 다시 시도하세요." },
  zh: { calling: "正在呼叫…", incomingVoice: "语音来电", incomingVideo: "视频来电", cancel: "取消通话", answer: "接听", decline: "拒绝", end: "结束通话", microphoneOn: "打开麦克风", microphoneOff: "关闭麦克风", cameraOn: "打开摄像头", cameraOff: "关闭摄像头", connectionError: "无法连接通话。请检查麦克风/摄像头权限后重试。" },
  es: { calling: "Llamando…", incomingVoice: "Llamada de voz entrante", incomingVideo: "Videollamada entrante", cancel: "Cancelar llamada", answer: "Contestar", decline: "Rechazar", end: "Finalizar llamada", microphoneOn: "Activar micrófono", microphoneOff: "Silenciar micrófono", cameraOn: "Activar cámara", cameraOff: "Desactivar cámara", connectionError: "No se pudo conectar a la llamada. Comprueba los permisos de micrófono y cámara e inténtalo de nuevo." },
  fr: { calling: "Appel en cours…", incomingVoice: "Appel vocal entrant", incomingVideo: "Appel vidéo entrant", cancel: "Annuler l'appel", answer: "Répondre", decline: "Refuser", end: "Terminer l'appel", microphoneOn: "Activer le micro", microphoneOff: "Couper le micro", cameraOn: "Activer la caméra", cameraOff: "Désactiver la caméra", connectionError: "Impossible de rejoindre l'appel. Vérifiez les autorisations du micro et de la caméra, puis réessayez." },
  de: { calling: "Anruf läuft…", incomingVoice: "Eingehender Sprachanruf", incomingVideo: "Eingehender Videoanruf", cancel: "Anruf abbrechen", answer: "Annehmen", decline: "Ablehnen", end: "Anruf beenden", microphoneOn: "Mikrofon einschalten", microphoneOff: "Mikrofon stummschalten", cameraOn: "Kamera einschalten", cameraOff: "Kamera ausschalten", connectionError: "Verbindung zum Anruf fehlgeschlagen. Prüfen Sie die Mikrofon-/Kameraberechtigung und versuchen Sie es erneut." },
  th: { calling: "กำลังโทร…", incomingVoice: "สายเสียงเข้า", incomingVideo: "สายวิดีโอเข้า", cancel: "ยกเลิกสาย", answer: "รับสาย", decline: "ปฏิเสธ", end: "วางสาย", microphoneOn: "เปิดไมโครโฟน", microphoneOff: "ปิดเสียงไมโครโฟน", cameraOn: "เปิดกล้อง", cameraOff: "ปิดกล้อง", connectionError: "ไม่สามารถเชื่อมต่อสายได้ โปรดตรวจสอบสิทธิ์ไมโครโฟน/กล้องแล้วลองอีกครั้ง" },
  id: { calling: "Memanggil…", incomingVoice: "Panggilan suara masuk", incomingVideo: "Panggilan video masuk", cancel: "Batalkan panggilan", answer: "Jawab", decline: "Tolak", end: "Akhiri panggilan", microphoneOn: "Nyalakan mikrofon", microphoneOff: "Bisukan mikrofon", cameraOn: "Nyalakan kamera", cameraOff: "Matikan kamera", connectionError: "Tidak dapat terhubung ke panggilan. Periksa izin mikrofon/kamera dan coba lagi." },
  pt: { calling: "Chamando…", incomingVoice: "Chamada de voz recebida", incomingVideo: "Chamada de vídeo recebida", cancel: "Cancelar chamada", answer: "Atender", decline: "Recusar", end: "Encerrar chamada", microphoneOn: "Ligar microfone", microphoneOff: "Silenciar microfone", cameraOn: "Ligar câmera", cameraOff: "Desligar câmera", connectionError: "Não foi possível conectar à chamada. Verifique as permissões do microfone/câmera e tente novamente." },
  ru: { calling: "Вызов…", incomingVoice: "Входящий аудиозвонок", incomingVideo: "Входящий видеозвонок", cancel: "Отменить звонок", answer: "Ответить", decline: "Отклонить", end: "Завершить звонок", microphoneOn: "Включить микрофон", microphoneOff: "Выключить микрофон", cameraOn: "Включить камеру", cameraOff: "Выключить камеру", connectionError: "Не удалось подключиться к звонку. Проверьте разрешения микрофона/камеры и повторите попытку." },
  ar: { calling: "جارٍ الاتصال…", incomingVoice: "مكالمة صوتية واردة", incomingVideo: "مكالمة فيديو واردة", cancel: "إلغاء المكالمة", answer: "رد", decline: "رفض", end: "إنهاء المكالمة", microphoneOn: "تشغيل الميكروفون", microphoneOff: "كتم الميكروفون", cameraOn: "تشغيل الكاميرا", cameraOff: "إيقاف الكاميرا", connectionError: "تعذر الاتصال بالمكالمة. تحقق من أذونات الميككروفون/الكاميرا وحاول مرة أخرى." },
  hi: { calling: "कॉल हो रही है…", incomingVoice: "इनकमिंग वॉइस कॉल", incomingVideo: "इनकमिंग वीडियो कॉल", cancel: "कॉल रद्द करें", answer: "कॉल लें", decline: "अस्वीकार करें", end: "कॉल समाप्त करें", microphoneOn: "माइक्रोफ़ोन चालू करें", microphoneOff: "माइक्रोफ़ोन म्यूट करें", cameraOn: "कैमरा चालू करें", cameraOff: "कैमरा बंद करें", connectionError: "कॉल से कनेक्ट नहीं हो सका। माइक्रोफ़ोन/कैमरा अनुमति जाँचें और फिर प्रयास करें।" },
};

export const callText = (language: LanguageCode) => callCopy[language];
