# Scene Shuffle Logic

## 1. Mục tiêu file

Tài liệu này định nghĩa cách phát hiện scene, biến raw scene thành clip usable, lọc clip không phù hợp và xáo trộn clip sao cho video final vẫn mượt, không quá random và còn giữ được đồng bộ audio/video ở mức chấp nhận được.

## 2. Nội dung chính

### Nguyên tắc detect scene và dùng scene như thế nào

MVP dùng scene detection kiểu content-based trên processed master đã tăng tốc 1.2x. Mục tiêu không phải “hiểu nội dung”, mà là tìm ranh giới thị giác đủ ổn định để cắt video thành các clip ngắn có thể tái sắp xếp.

Scene sau khi detect không được dùng trực tiếp ngay. Hệ thống phải qua một bước chuẩn hóa thành `usable scene units`, vì raw scene thường có nhiều đoạn quá ngắn, quá dài hoặc bị cắt gãy thiếu ổn định.

### Scene nào giữ, scene nào bỏ

Nguyên tắc giữ scene cho MVP:

- Giữ scene có thời lượng đủ xem được và không gây cảm giác nháy.
- Giữ scene có khung hình hợp lệ, không phải đoạn lỗi decode hoặc đoạn gần như đen hoàn toàn.
- Giữ scene có nội dung chuyển động hoặc bố cục đủ rõ để làm clip độc lập.

Nguyên tắc bỏ scene cho MVP:

- Bỏ scene quá ngắn không đủ tạo một nhịp xem tối thiểu.
- Bỏ scene gần như đen hoàn toàn, trắng hoàn toàn hoặc lỗi khung hình kéo dài.
- Bỏ scene là mảnh cắt lỗi do detector quá nhạy tạo ra.

MVP không cố chấm điểm semantic như “cảnh đẹp”, “cảnh có người”, “cảnh bán hàng tốt”.

### Xử lý scene quá ngắn, quá dài, xấu, không usable

Quy tắc chuẩn hóa scene unit:

- Scene dưới khoảng `0.9 giây` không dùng trực tiếp.
- Nếu scene ngắn đứng cạnh một scene liền kề và tổng thời lượng sau gộp vẫn hợp lý, ưu tiên merge để tạo clip usable.
- Scene dài hơn khoảng `4.5 giây` nên được chia thành các sub-clip ngắn hơn để tăng tính shuffle.
- Phần đuôi còn lại quá ngắn sau khi split nên gộp ngược vào sub-clip trước đó thay vì để thành clip mới.

Scene không usable gồm:

- Đoạn gần như black frame hoặc blank frame.
- Đoạn lỗi decode.
- Đoạn freeze kéo dài bất thường nếu heuristic kỹ thuật phát hiện được.

### Logic xáo trộn

Chiến lược MVP là `constrained shuffle`, không phải random hoàn toàn.

Trình tự áp dụng:

1. Chọn một opener từ nhóm scene đầu nguồn có chất lượng ổn và thời lượng dễ xem.
2. Chọn một closer từ nhóm scene cuối nguồn để giữ cảm giác video có điểm kết.
3. Các scene còn lại được xáo trộn bằng seed.
4. Sau khi shuffle, áp các ràng buộc adjacency để sửa lại thứ tự nếu cần.

Ràng buộc adjacency cho MVP:

- Không để hai scene vốn liền nhau trong nguồn đứng cạnh nhau trong output nếu có lựa chọn khác.
- Hạn chế hai scene liên tiếp có chênh lệch thời lượng quá lớn.
- Hạn chế chuỗi nhiều scene liên tiếp đến từ cùng một vùng thời gian của video gốc.

### Cách giữ video sau shuffle vẫn xem mượt

- Duy trì scene unit trong vùng thời lượng trung bình dễ xem, không quá vụn.
- Dùng hard cut giữa các scene thay vì transition phức tạp ở MVP.
- Áp micro fade rất ngắn cho audio ở biên clip để tránh click.
- Giữ opener và closer có chủ đích để video có nhịp vào và ra tự nhiên hơn.

### Cách tránh cảm giác random quá mức

MVP tránh cảm giác random bằng ba nguyên tắc:

- Không shuffle hoàn toàn từ đầu tới cuối.
- Không để các clip có thời lượng cực lệch nhau nối liên tục quá nhiều.
- Không để nhiều clip từ cùng một khu vực nguồn gom thành cụm dài.

Video final vì vậy vẫn tạo cảm giác “biến thể” nhưng chưa biến thành montage hỗn loạn.

### Cách giữ đồng bộ audio/video ở mức chấp nhận được

Mỗi scene unit phải giữ cặp audio/video đi cùng nhau. Khi scene được shuffle, audio của chính scene đó được shuffle theo. Cách này giữ sync ở cấp từng clip, chấp nhận rằng mạch lời thoại hoặc nhạc của video có thể không còn theo câu chuyện gốc.

Để tránh cảm giác ghép gắt:

- Áp fade biên audio rất ngắn ở đầu và cuối clip.
- Không dùng crossfade dài ở MVP vì dễ làm lệch thời lượng.

### Chiến lược MVP

Chiến lược scene cho MVP là:

- Detect scene theo thay đổi hình ảnh.
- Chuẩn hóa scene thành clip usable bằng merge và split đơn giản.
- Shuffle theo kiểu constrained shuffle.
- Giữ cặp A/V cùng clip để ưu tiên sync cục bộ thay vì trung thành với audio gốc toàn tuyến.

## 3. Quyết định thiết kế chính

- Chọn `scene-based A/V shuffle` thay vì chỉ xáo trộn video.
- Chọn `constrained shuffle` thay vì random hoàn toàn.
- Chọn `hard cut + micro audio fade` thay vì transition phức tạp.
- Chọn `merge/split theo ngưỡng thời lượng` thay vì scoring AI.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Detector quá nhạy sẽ tạo quá nhiều scene rất ngắn, làm plan khó dùng.
- Detector quá lì sẽ tạo quá ít scene, làm shuffle thiếu tác dụng.
- Merge và split scene nếu thiếu nhất quán sẽ gây nhầm timeline khi extract clip.
- Shuffle A/V theo clip giữ sync cục bộ nhưng có thể làm mạch thoại mất tự nhiên. MVP chấp nhận điều này trong giới hạn nội dung ngắn.

## 5. Tiêu chí hoàn thành

- File này phải chốt rõ cách detect, lọc, merge, split và shuffle scene.
- Phải nêu được cách giữ video xem mượt và tránh cảm giác random quá mức.
- Phải giải thích rõ cách giữ đồng bộ audio/video ở mức chấp nhận được.
- Builder AI đọc file này phải có đủ logic để hiện thực scene planner cho MVP mà không cần suy nghĩ lại từ đầu.
