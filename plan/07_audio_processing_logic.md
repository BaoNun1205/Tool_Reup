# Audio Processing Logic

## 1. Mục tiêu file

Tài liệu này xác định mục tiêu xử lý audio, ảnh hưởng của việc tăng tốc 1.2x, chiến lược tăng âm lượng an toàn, cách tránh clipping và quan hệ giữa audio với scene shuffle. Đây là file khóa logic audio cho MVP để tránh xử lý quá tay hoặc tạo méo tiếng.

## 2. Nội dung chính

### Mục tiêu xử lý audio

Mục tiêu audio của MVP là:

- Giữ audio nghe rõ hơn so với nguồn trong khả năng an toàn.
- Tăng độ lớn cảm nhận ở mức vừa đủ cho short-form video.
- Tránh clipping, pumping mạnh hoặc méo tiếng rõ rệt.
- Giữ đồng bộ audio/video của final cut ở mức chấp nhận được.

MVP không hướng tới mastering chuyên sâu, tách stem, khử ồn thông minh hoặc tối ưu riêng cho thoại và nhạc.

### Tăng tốc 1.2x ảnh hưởng audio ra sao

Khi video tăng tốc 1.2x, audio cũng phải rút ngắn thời lượng tương ứng. Nếu tăng tốc kiểu thô, pitch sẽ cao lên và giọng nói dễ nghe gắt. Vì vậy, MVP nên dùng chiến lược time-stretch phù hợp để rút ngắn thời lượng nhưng hạn chế thay đổi pitch quá mức.

Việc tăng tốc cũng làm ngưỡng cảm nhận loudness thay đổi nhẹ, nên kiểm soát loudness phải được đánh giá trên final cut chứ không chỉ trên source audio.

### Tăng âm lượng kiểu nào là an toàn

Chiến lược an toàn cho MVP:

- Phân tích loudness và peak của audio sau khi đã tăng tốc và ghép final cut.
- Nếu audio thấp hơn mục tiêu, áp tăng gain có kiểm soát.
- Dùng dynamic control nhẹ và limiter trần an toàn để tránh méo đỉnh.
- Nếu audio vốn đã to, hệ thống phải attenuate hoặc giữ nguyên, không cố boost thêm.

MVP ưu tiên loudness “ổn và an toàn” hơn là “to tối đa”.

### Khi nào normalize, khi nào boost

Nguyên tắc xử lý:

- `Normalize` dùng để đưa audio final về một mức loudness mục tiêu ổn định giữa các job.
- `Boost` chỉ dùng khi audio thấp hơn đáng kể và vẫn còn headroom an toàn.
- Nếu audio đã gần hoặc vượt mục tiêu, không boost, chỉ cân về trần an toàn.

Mức mục tiêu khuyến nghị cho MVP là loudness social-friendly vừa phải, ví dụ quanh vùng `-16 LUFS integrated`, với trần peak an toàn khoảng `-1.5 dBTP`. Đây là mức dễ chấp nhận hơn so với cố ép audio rất to.

### Cách tránh clipping, méo, quá to

Để tránh clipping và méo:

- Không cộng gain cố định một cách mù quáng.
- Luôn phân tích peak trước khi boost.
- Có limiter trần an toàn ở cuối chuỗi xử lý.
- Giới hạn mức gain cộng thêm tối đa để tránh kéo noise hoặc méo nguồn lên quá rõ.

Nếu audio nguồn đã méo hoặc clip sẵn, MVP không thể sửa triệt để. Trong trường hợp đó, hệ thống nên ưu tiên không làm vấn đề tệ hơn.

### Audio output nào cần sinh ra

Artifact audio bắt buộc của MVP:

- `final_audio.m4a`: audio final đã đi qua tốc độ, scene assembly và loudness safety.

Metadata phải ghi rõ:

- loudness target dùng cho job
- cảnh báo nếu audio nguồn thiếu, quá nhỏ hoặc đã méo
- trạng thái xử lý audio final

### Relation giữa audio của các scene sau khi shuffle

Audio phải đi cùng video ở cấp scene unit. Khi scene bị shuffle, audio của scene đó cũng bị shuffle theo. Cách này giữ sync cục bộ tốt hơn nhiều so với giữ nguyên audio tuyến tính của video gốc.

Tại ranh giới scene:

- Dùng fade rất ngắn ở đầu và cuối mỗi clip để tránh tiếng click.
- Không dùng crossfade dài trong MVP vì dễ kéo lệch timing và làm mờ nhịp cắt.

### Chiến lược audio cho MVP

Chiến lược audio được chốt cho MVP:

- Tăng tốc audio đồng bộ với video bằng time-stretch phù hợp.
- Cắt audio theo scene unit và shuffle cùng video.
- Ghép final cut trước, sau đó mới làm loudness finishing ở cấp final.
- Áp tăng âm lượng theo phân tích loudness và peak, không cộng gain cố định.
- Dùng limiter trần an toàn để tránh clipping.

## 3. Quyết định thiết kế chính

- Chọn `time-stretch đồng bộ` thay vì speed-up làm đổi pitch quá rõ.
- Chọn `scene-paired audio` thay vì giữ nguyên audio gốc toàn tuyến.
- Chọn `final loudness finishing` sau khi đã ghép xong final cut.
- Chọn `normalize an toàn` thay vì ép âm lượng tối đa.
- Không làm EQ, denoise hay mastering thông minh trong MVP vì rủi ro artifact cao.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Audio nguồn TikTok rất không đồng nhất, nhiều clip đã bị nén hoặc méo từ trước.
- Nếu loudness xử lý ở sai stage, audio final có thể lệch cảm nhận sau khi shuffle.
- Gain quá mạnh dễ kéo noise lên hoặc làm lộ méo nguồn.
- Nếu fade biên clip không được tính cẩn thận, audio final sẽ có tiếng click giữa các scene.

## 5. Tiêu chí hoàn thành

- File này phải giải thích rõ tác động của việc tăng tốc 1.2x lên audio.
- Phải chốt được chiến lược normalize, boost và limiter cho MVP.
- Phải mô tả rõ quan hệ giữa audio và scene shuffle.
- Builder AI đọc file này phải hiểu rằng audio của MVP cần an toàn, ổn định và không xử lý quá tay.
