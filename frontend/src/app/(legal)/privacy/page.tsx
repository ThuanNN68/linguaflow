import Link from "next/link";

const sections = [
  ["1. Dữ liệu chúng tôi xử lý", "Để vận hành LinguaFlow, chúng tôi có thể xử lý thông tin hồ sơ, lựa chọn ngôn ngữ, nội dung tin nhắn, tệp bạn chủ động chia sẻ, thông tin sự kiện/lời nhắc và dữ liệu kỹ thuật cần thiết để duy trì phiên đăng nhập."],
  ["2. Cách dữ liệu được sử dụng", "Dữ liệu được dùng để xác thực tài khoản, hiển thị cuộc trò chuyện, cung cấp bản dịch theo ngôn ngữ đã chọn, gửi thông báo trong ứng dụng và vận hành lịch cá nhân. Chúng tôi không bán nội dung hoặc dữ liệu cá nhân của bạn cho bên thứ ba."],
  ["3. Dịch vụ bên thứ ba", "Nội dung cần dịch có thể được gửi tới nhà cung cấp mô hình/ngôn ngữ để tạo bản dịch. Chúng tôi chỉ chia sẻ dữ liệu cần thiết để cung cấp chức năng và yêu cầu các nhà cung cấp xử lý dữ liệu theo phạm vi dịch vụ của họ."],
  ["4. Lưu giữ và bảo vệ", "Chúng tôi lưu dữ liệu trong thời gian cần thiết để vận hành dịch vụ hoặc đáp ứng yêu cầu hợp pháp. Dù áp dụng các biện pháp kỹ thuật và tổ chức hợp lý, không phương thức truyền hoặc lưu trữ nào có thể bảo đảm an toàn tuyệt đối."],
  ["5. Lựa chọn của bạn", "Bạn có thể cập nhật thông tin tài khoản, ngừng sử dụng dịch vụ và yêu cầu hỗ trợ liên quan đến dữ liệu cá nhân. Hãy liên hệ với chúng tôi để được hỗ trợ."],
  ["6. Thay đổi chính sách", "Chúng tôi có thể cập nhật chính sách này khi sản phẩm phát triển hoặc khi có thay đổi về pháp lý. Phiên bản mới sẽ được công bố tại trang này cùng ngày cập nhật tương ứng."],
];

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-800 sm:px-6 sm:py-12">
      <article className="mx-auto max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-200/50">
        <header className="border-b border-slate-100 bg-gradient-to-br from-blue-50 via-white to-indigo-50 px-6 py-10 sm:px-12 sm:py-14">
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-blue-700 hover:text-blue-900"><span aria-hidden="true">←</span> LinguaFlow</Link>
          <p className="mt-8 text-sm font-semibold uppercase tracking-[0.16em] text-blue-600">Pháp lý & quyền riêng tư</p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">Chính sách quyền riêng tư</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">Minh bạch về dữ liệu LinguaFlow cần để cung cấp trò chuyện, dịch ngôn ngữ và lịch cá nhân.</p>
          <p className="mt-5 text-sm text-slate-500">Cập nhật lần cuối: 30 tháng 8, 2026</p>
        </header>

        <div className="px-6 py-9 sm:px-12 sm:py-12">
          <div className="rounded-2xl border border-blue-100 bg-blue-50 px-5 py-4 text-sm leading-6 text-blue-950">LinguaFlow đang được phát triển. Vui lòng không gửi thông tin nhạy cảm hoặc nội dung bạn không muốn được xử lý để vận hành dịch vụ.</div>
          <div className="mt-10 space-y-10 text-[15px] leading-7 text-slate-600">
            {sections.map(([title, content]) => <section key={title}><h2 className="text-xl font-bold tracking-tight text-slate-900">{title}</h2><p className="mt-3">{content}</p></section>)}
          </div>
          <footer className="mt-12 rounded-2xl bg-slate-900 px-6 py-6 text-slate-100">
            <h2 className="font-semibold">Liên hệ về quyền riêng tư</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">Gửi câu hỏi hoặc yêu cầu liên quan đến dữ liệu cá nhân tới <a className="font-medium text-white underline underline-offset-4" href="mailto:thuan669978@gmail.com">thuan669978@gmail.com</a>.</p>
            <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-sm font-medium"><Link href="/terms" className="text-white hover:underline">Điều khoản dịch vụ</Link><Link href="/register" className="text-white hover:underline">Quay lại đăng ký</Link></div>
          </footer>
        </div>
      </article>
    </main>
  );
}
