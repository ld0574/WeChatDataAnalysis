param(
    [Parameter(Mandatory=$true)][string]$ImagePath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$Language
)
$ErrorActionPreference = 'Stop'

try {
    if (Test-Path -LiteralPath $OutputPath) { throw 'windows_ocr_output_already_exists' }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
    $taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-Operation($Operation, [Type]$ResultType) {
        $task = $taskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
        [void]$task.Wait()
        return $task.Result
    }
    $file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Language))
    if ($null -eq $engine) { throw 'windows_ocr_local_language_unavailable' }
    $result = Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @($result.Lines | ForEach-Object {
        [ordered]@{
            text = $_.Text
            words = @($_.Words | ForEach-Object {
                [ordered]@{text=$_.Text;box=@($_.BoundingRect.X,$_.BoundingRect.Y,$_.BoundingRect.Width,$_.BoundingRect.Height)}
            })
        }
    })
    $receipt = [ordered]@{
        policy='windows-installed-offline-ocr-draft-v1'
        image_path=$ImagePath
        image_sha256=(Get-FileHash -LiteralPath $ImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
        language=$Language
        engine_language=$engine.RecognizerLanguage.LanguageTag
        maximum_image_dimension=[Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        width=$bitmap.PixelWidth
        height=$bitmap.PixelHeight
        os_version=[Environment]::OSVersion.Version.ToString()
        lines=$lines
        original_negative_retained=$true
        gpu_calls=0
        semantic_acceptance=$false
        reader_publication_allowed=$false
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    [ordered]@{state='installed_windows_ocr_draft_saved';language=$Language;line_count=$lines.Count;word_count=@($result.Lines.Words).Count;gpu_calls=0} | ConvertTo-Json -Compress
    $bitmap.Dispose()
    $stream.Dispose()
} catch {
    [ordered]@{state='stopped';error_type=$_.Exception.GetType().Name} | ConvertTo-Json -Compress
    exit 1
}
