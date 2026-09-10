import Link from "next/link";

const sections = [
  ["1. Chấp nhận điều khoản", "Bằng việc tạo tài khoản hoặc sử dụng LinguaFlow, bạn xác nhận đã đọc và đồng ý với Điều khoản dịch vụ này cùng Chính sách quyền riêng tư. Nếu không đồng ý, vui lòng không sử dụng dịch vụ."],
  ["2. Dịch vụ của LinguaFlow", "LinguaFlow cung cấp công cụ trò chuyện, hỗ trợ dịch ngôn ngữ, lịch cá nhân và các tính năng phối hợp liên quan."],
  ["3. Tài khoản và bảo mật", "Bạn chịu trách nhiệm bảo vệ thông tin đăng nhập và các hoạt động diễn ra dưới tài khoản của mình. Bạn cần cung cấp thông tin chính xác, không mạo danh người khác, và thông báo cho chúng tôi khi phát hiện truy cập trái phép."],
  ["4. Nội dung và cách sử dụng chấp nhận được", "Bạn không được sử dụng LinguaFlow để đăng tải hoặc truyền tải nội dung trái pháp luật, quấy rối, xâm phạm quyền riêng tư, quyền sở hữu trí tuệ hoặc quyền hợp pháp của người khác. Không tải lên mã độc, cố gắng can thiệp hệ thống hay dùng dịch vụ ngoài mục đích được thiết kế."],
  ["5. Bản dịch và nội dung do AI hỗ trợ", "Bản dịch hoặc nội dung được hỗ trợ bởi mô hình ngôn ngữ có thể không hoàn toàn chính xác. Với thông tin quan trọng về pháp lý, y tế, tài chính hoặc an toàn, bạn cần kiểm tra nguồn gốc và đưa ra quyết định độc lập; LinguaFlow không thay thế chuyên gia trong các lĩnh vực này."],
  ["6. Tính khả dụng và thay đổi dịch vụ", "Chúng tôi nỗ lực duy trì dịch vụ ổn định, nhưng không cam kết dịch vụ luôn không gián đoạn hoặc không có lỗi. Chúng tôi có thể thay đổi, tạm ngừng hoặc kết thúc một phần tính năng khi cần thiết, đồng thời sẽ cố gắng thông báo hợp lý nếu thay đổi ảnh hưởng đáng kể đến người dùng."],
  ["7. Giới hạn trách nhiệm", "Trong phạm vi pháp luật cho phép, LinguaFlow được cung cấp trên cơ sở hiện trạng. Chúng tôi không chịu trách nhiệm cho thiệt hại gián tiếp, ngẫu nhiên hoặc hậu quả phát sinh từ việc sử dụng hay không thể sử dụng dịch vụ."],
  ["8. Thay đổi điều khoản", "Chúng tôi có thể cập nhật Điều khoản dịch vụ để phản ánh thay đổi của sản phẩm hoặc yêu cầu pháp lý. Việc tiếp tục sử dụng dịch vụ sau khi phiên bản mới được công bố đồng nghĩa với việc bạn chấp nhận các điều khoản đã cập nhật."],
];

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-800 sm:px-6 sm:py-12">
      <article className="mx-auto max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-200/50">
        <header className="border-b border-slate-100 bg-gradient-to-br from-indigo-50 via-white to-violet-50 px-6 py-10 sm:px-12 sm:py-14">
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:text-indigo-900"><span aria-hidden="true">←</span> LinguaFlow</Link>
          <p className="mt-8 text-sm font-semibold uppercase tracking-[0.16em] text-indigo-600">Điều khoản sử dụng</p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">Điều khoản dịch vụ</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">Các điều khoản áp dụng khi bạn sử dụng LinguaFlow và các tính năng liên quan.</p>
          <p className="mt-5 text-sm text-slate-500">Có hiệu lực từ: 30 tháng 8, 2026</p>
        </header>

        <div className="px-6 py-9 sm:px-12 sm:py-12">
          <div className="rounded-2xl border border-indigo-100 bg-indigo-50 px-5 py-4 text-sm leading-6 text-indigo-950">Vui lòng đọc kỹ các điều khoản này trước khi sử dụng LinguaFlow. Nếu bạn có câu hỏi, hãy liên hệ với chúng tôi trước khi tiếp tục.</div>
          <div className="mt-10 space-y-10 text-[15px] leading-7 text-slate-600">
            {sections.map(([title, content]) => <section key={title}><h2 className="text-xl font-bold tracking-tight text-slate-900">{title}</h2><p className="mt-3">{content}</p></section>)}
          </div>
          <footer className="mt-12 rounded-2xl bg-slate-900 px-6 py-6 text-slate-100">
            <h2 className="font-semibold">Liên hệ</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">Liên hệ <a className="font-medium text-white underline underline-offset-4" href="mailto:thuan669978@gmail.com">thuan669978@gmail.com</a> nếu bạn có câu hỏi về các điều khoản này.</p>
            <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-sm font-medium"><Link href="/privacy" className="text-white hover:underline">Chính sách quyền riêng tư</Link><Link href="/register" className="text-white hover:underline">Quay lại đăng ký</Link></div>
          </footer>
        </div>
      </article>
    </main>
  );
}
