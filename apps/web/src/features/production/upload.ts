import { getBackend } from "@/lib/api/backend";
import type { VideoVersionView } from "@/lib/api/types";

const EXT_MIME: Record<string, string> = {
  mp4: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
  m4v: "video/x-m4v",
};

export function videoMime(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_MIME[ext] ?? (Object.values(EXT_MIME).includes(file.type) ? file.type : null);
}

export class UploadError extends Error {
  constructor(message: string) {
    super(message);
  }
}

/**
 * 上传视频：文件本身就是请求体（不用 multipart），用 XHR 以便显示进度。
 * 同一文件重复上传时后端返回已有版本（replayed = true）。
 */
export async function uploadVideo(
  roundId: string,
  file: File,
  note: string,
  onProgress: (fraction: number) => void,
): Promise<{ video: VideoVersionView; replayed: boolean }> {
  const mime = videoMime(file);
  if (!mime) throw new UploadError("只支持 MP4 / MOV / WebM / M4V 视频");
  if (file.size > 2 * 1024 ** 3) throw new UploadError("单个视频不能超过 2 GB");
  const { baseUrl, token } = await getBackend();
  const url = `${baseUrl}/api/v1/rounds/${roundId}/videos?note=${encodeURIComponent(note)}`;
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", mime);
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new UploadError("上传失败：连接不到本机后端"));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* 非 JSON */
      }
      if (xhr.status === 200 || xhr.status === 201) {
        resolve({ video: data as VideoVersionView, replayed: xhr.status === 200 });
      } else {
        const msg = (data as { error?: { message?: string } } | null)?.error?.message;
        reject(new UploadError(msg ?? `上传失败（${xhr.status}）`));
      }
    };
    xhr.send(file);
  });
}

export async function mediaSrc(mediaUrl: string): Promise<string> {
  const { baseUrl } = await getBackend();
  return `${baseUrl}${mediaUrl}`;
}
