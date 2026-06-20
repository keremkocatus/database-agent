/* Seed/demo MSSQL şeması (design/15 mantığı) — M1/M2 entegrasyon testi için.
   Kapsadığı senaryolar:
     - FK ilişkili tablolar + check constraint + extended property (MS_Description)
     - SP→SP çağrısı (calls), SP→tablo read/write ayrımı (is_updated)
     - View (veri kaynağı + kolon sözlüğü), scalar function, DML trigger
     - synonym (kapsam-içi hedefe)
     - bağımlılık döngüsü (SP_A↔SP_B) — cycle guard testi
   Salt-okunur servis hesabı: svc_catalog_ro (design/02 grant script).
*/

IF DB_ID('DemoDB') IS NULL
    CREATE DATABASE DemoDB;
GO
USE DemoDB;
GO

/* --- Tablolar --------------------------------------------------------- */
IF OBJECT_ID('dbo.KULLANICI') IS NULL
CREATE TABLE dbo.KULLANICI (
    ID          INT IDENTITY(1,1) PRIMARY KEY,
    AdSoyad     NVARCHAR(200) NOT NULL,
    Email       NVARCHAR(200) NULL
);
GO

IF OBJECT_ID('dbo.TEKLIF') IS NULL
CREATE TABLE dbo.TEKLIF (
    TeklifNo    INT IDENTITY(1,1) PRIMARY KEY,
    KullaniciID INT NOT NULL,
    Sure        INT NULL,
    Durum       CHAR(1) NOT NULL CONSTRAINT CK_TEKLIF_Durum CHECK (Durum IN ('A','P','I')),
    BrutPrim    DECIMAL(18,2) NULL,
    Vergi       DECIMAL(18,2) NULL,
    ToplamPrim  AS (ISNULL(BrutPrim,0) + ISNULL(Vergi,0)),
    CONSTRAINT FK_TEKLIF_KULLANICI FOREIGN KEY (KullaniciID) REFERENCES dbo.KULLANICI(ID)
);
GO

IF OBJECT_ID('dbo.TEKLIF_LOG') IS NULL
CREATE TABLE dbo.TEKLIF_LOG (
    LogID       INT IDENTITY(1,1) PRIMARY KEY,
    TeklifNo    INT NOT NULL,
    Mesaj       NVARCHAR(400) NULL,
    Tarih       DATETIME NOT NULL DEFAULT GETDATE()
);
GO

/* Extended property (otoriter insan dokümantasyonu, design/03) */
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE major_id = OBJECT_ID('dbo.TEKLIF')
               AND minor_id = COLUMNPROPERTY(OBJECT_ID('dbo.TEKLIF'),'Sure','ColumnId') AND name='MS_Description')
    EXEC sys.sp_addextendedproperty @name=N'MS_Description', @value=N'Teklif süresi (gün)',
        @level0type=N'SCHEMA', @level0name=N'dbo',
        @level1type=N'TABLE',  @level1name=N'TEKLIF',
        @level2type=N'COLUMN', @level2name=N'Sure';
GO

/* --- View (veri kaynağı) ---------------------------------------------- */
IF OBJECT_ID('dbo.VW_AKTIF_TEKLIF') IS NOT NULL DROP VIEW dbo.VW_AKTIF_TEKLIF;
GO
CREATE VIEW dbo.VW_AKTIF_TEKLIF AS
    SELECT t.TeklifNo, t.Sure, t.Durum, k.AdSoyad, k.Email
    FROM dbo.TEKLIF t
    JOIN dbo.KULLANICI k ON k.ID = t.KullaniciID
    WHERE t.Durum = 'A';
GO

/* --- Scalar function -------------------------------------------------- */
IF OBJECT_ID('dbo.FN_TOPLAM_PRIM') IS NOT NULL DROP FUNCTION dbo.FN_TOPLAM_PRIM;
GO
CREATE FUNCTION dbo.FN_TOPLAM_PRIM(@TeklifNo INT)
RETURNS DECIMAL(18,2)
AS
BEGIN
    DECLARE @sonuc DECIMAL(18,2);
    SELECT @sonuc = ISNULL(BrutPrim,0) + ISNULL(Vergi,0) FROM dbo.TEKLIF WHERE TeklifNo = @TeklifNo;
    RETURN @sonuc;
END;
GO

/* --- Yetki kontrol SP'si (çağrılan) ----------------------------------- */
IF OBJECT_ID('dbo.SP_KULLANICI_YETKI_KONTROL') IS NOT NULL DROP PROCEDURE dbo.SP_KULLANICI_YETKI_KONTROL;
GO
CREATE PROCEDURE dbo.SP_KULLANICI_YETKI_KONTROL @KullaniciID INT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT ID, AdSoyad FROM dbo.KULLANICI WHERE ID = @KullaniciID;
END;
GO

/* --- Ana SP: okur (TEKLIF) + yazar (TEKLIF_LOG) + çağırır (yetki SP) --- */
IF OBJECT_ID('dbo.SP_TEKLIF_SURELERI') IS NOT NULL DROP PROCEDURE dbo.SP_TEKLIF_SURELERI;
GO
CREATE PROCEDURE dbo.SP_TEKLIF_SURELERI
    @KullaniciID INT,
    @Durum CHAR(1) = 'A'
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.SP_KULLANICI_YETKI_KONTROL @KullaniciID;

    CREATE TABLE #GeciciSure (TeklifNo INT, Sure INT);
    INSERT INTO #GeciciSure (TeklifNo, Sure)
        SELECT TeklifNo, Sure FROM dbo.TEKLIF
        WHERE KullaniciID = @KullaniciID AND Durum = @Durum;

    INSERT INTO dbo.TEKLIF_LOG (TeklifNo, Mesaj)
        SELECT TeklifNo, 'sorgulandi' FROM #GeciciSure;

    SELECT TeklifNo, Sure FROM #GeciciSure;
END;
GO

/* --- Döngü senaryosu (cycle guard testi): SP_A ↔ SP_B ----------------- */
IF OBJECT_ID('dbo.SP_A') IS NOT NULL DROP PROCEDURE dbo.SP_A;
GO
IF OBJECT_ID('dbo.SP_B') IS NOT NULL DROP PROCEDURE dbo.SP_B;
GO
CREATE PROCEDURE dbo.SP_A AS BEGIN SET NOCOUNT ON; EXEC dbo.SP_B; END;
GO
CREATE PROCEDURE dbo.SP_B AS BEGIN SET NOCOUNT ON; EXEC dbo.SP_A; END;
GO

/* --- DML trigger ------------------------------------------------------ */
IF OBJECT_ID('dbo.TR_TEKLIF_LOG') IS NOT NULL DROP TRIGGER dbo.TR_TEKLIF_LOG;
GO
CREATE TRIGGER dbo.TR_TEKLIF_LOG ON dbo.TEKLIF AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.TEKLIF_LOG (TeklifNo, Mesaj)
        SELECT TeklifNo, 'eklendi' FROM inserted;
END;
GO

/* --- Synonym (kapsam-içi hedef) --------------------------------------- */
IF OBJECT_ID('dbo.TEKLIF_SYN') IS NOT NULL DROP SYNONYM dbo.TEKLIF_SYN;
GO
CREATE SYNONYM dbo.TEKLIF_SYN FOR dbo.TEKLIF;
GO

/* --- Salt-okunur servis hesabı (design/02 grant script) --------------- */
USE master;
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'svc_catalog_ro')
    CREATE LOGIN svc_catalog_ro WITH PASSWORD = 'Demo_Catalog_ro_2026!', CHECK_POLICY = OFF;
GO
USE DemoDB;
GO
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'svc_catalog_ro')
    CREATE USER svc_catalog_ro FOR LOGIN svc_catalog_ro;
GO
GRANT VIEW DEFINITION TO svc_catalog_ro;
GRANT VIEW DATABASE STATE TO svc_catalog_ro;
GO
PRINT 'DemoDB seed tamam.';
GO
