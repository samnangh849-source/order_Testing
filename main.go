package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	// Consider adding a Go Telegram library, e.g., "github.com/go-telegram-bot-api/telegram-bot-api/v5"
	// Consider adding a PDF library if needed, e.g., "github.com/jung-kurt/gofpdf"
)

// --- Configuration ---
var (
	appsScriptURL    string
	appsScriptSecret string
	// Add Telegram Bot Tokens/Group IDs here from environment variables
	telegramConfig = make(map[string]map[string]string) // e.g., map[TeamName]map["token"/"groupID"]
	renderBaseURL    string                               // URL of this Render service itself
)

// --- Constants from Apps Script Config (Keep consistent) ---
const (
    AllOrdersSheet     = "AllOrders"
    FormulaReportSheet = "FormulaReport"
    RevenueSheet       = "RevenueDashboard" // *** IMPORTANT ***
    UserActivitySheet  = "UserActivityLogs"
    TelegramTemplatesSheet = "TelegramTemplates"
    // ... add others if needed directly in Go
)


// --- Cache ---
type CacheItem struct {
	Data      interface{}
	ExpiresAt time.Time
}

var (
	cache      = make(map[string]CacheItem)
	cacheMutex sync.RWMutex
	cacheTTL   = 5 * time.Minute // Default cache duration
)

func setCache(key string, data interface{}, duration time.Duration) {
	cacheMutex.Lock()
	defer cacheMutex.Unlock()
	cache[key] = CacheItem{
		Data:      data,
		ExpiresAt: time.Now().Add(duration),
	}
	log.Printf("Cache SET for key: %s", key)
}

func getCache(key string) (interface{}, bool) {
	cacheMutex.RLock()
	defer cacheMutex.RUnlock()
	item, found := cache[key]
	if !found || time.Now().After(item.ExpiresAt) {
		if found {
			log.Printf("Cache EXPIRED for key: %s", key)
		} else {
			//log.Printf("Cache MISS for key: %s", key) // Too noisy maybe
		}
		return nil, false
	}
	log.Printf("Cache HIT for key: %s", key)
	return item.Data, true
}

func clearCache() {
    cacheMutex.Lock()
    defer cacheMutex.Unlock()
    cache = make(map[string]CacheItem)
    log.Println("Cache CLEARED")
}
// Function to invalidate specific sheet cache
func invalidateSheetCache(sheetName string) {
    cacheMutex.Lock()
    defer cacheMutex.Unlock()
    delete(cache, "sheet_"+sheetName)
    log.Printf("Cache INVALIDATED for key: sheet_%s", sheetName)
}


// --- Models (Adjust based on your actual Sheet headers) ---
// Use `json:"Header Name"` if header has spaces or special chars
type User struct {
	UserName          string `json:"UserName"`
	Password          string `json:"Password"`
	Team              string `json:"Team"` // Comma-separated
	FullName          string `json:"FullName"`
	ProfilePictureURL string `json:"ProfilePictureURL"`
	Role              string `json:"Role"`
	IsSystemAdmin     bool   `json:"IsSystemAdmin"`
}

type Product struct {
	ProductName string  `json:"ProductName"`
	Barcode     string  `json:"Barcode"`
	Price       float64 `json:"Price"`
	ImageURL    string  `json:"ImageURL"`
}

type Location struct {
	Province string `json:"Province"`
	District string `json:"District"`
	Sangkat  string `json:"Sangkat"`
}

type ShippingMethod struct {
    MethodName            string `json:"MethodName"`
    LogoURL               string `json:"LogoURL"`
    AllowManualDriver     bool   `json:"AllowManualDriver"`
    RequireDriverSelection bool   `json:"RequireDriverSelection"`
}

type TeamPage struct {
    Team           string `json:"Team"`
    PageName       string `json:"PageName"`
    TelegramValue string `json:"TelegramValue"`
}

type Color struct {
    ColorName string `json:"ColorName"`
}
type Driver struct {
    DriverName string `json:"DriverName"`
    ImageURL   string `json:"ImageURL"`
}
type BankAccount struct {
    BankName string `json:"BankName"`
    LogoURL  string `json:"LogoURL"`
}
type PhoneCarrier struct {
    CarrierName string `json:"CarrierName"`
    Prefixes    string `json:"Prefixes (comma-separated)"` // Use exact header name
    CarrierLogoURL string `json:"CarrierLogoURL"`
}
type TelegramTemplate struct {
    Team     string `json:"Team"`
    Part     int    `json:"Part"` // Assuming Part is a number
    Template string `json:"Template"`
}

// *** Model for Order data read from AllOrders (for FormulaReport) ***
type Order struct {
	Timestamp            string  `json:"Timestamp"` // Read as ISO string
	OrderID              string  `json:"Order ID"`
	User                 string  `json:"User"`
	Page                 string  `json:"Page"`
	TelegramValue        string  `json:"TelegramValue"`
	CustomerName         string  `json:"Customer Name"`
	CustomerPhone        string  `json:"Customer Phone"`
	Location             string  `json:"Location"`
	AddressDetails       string  `json:"Address Details"`
	Note                 string  `json:"Note"`
	ShippingFeeCustomer float64 `json:"Shipping Fee (Customer)"`
	Subtotal             float64 `json:"Subtotal"`
	GrandTotal           float64 `json:"Grand Total"`
	ProductsJSON         string  `json:"Products (JSON)"`
	InternalShippingMethod string `json:"Internal Shipping Method"`
	InternalShippingDetails string `json:"Internal Shipping Details"`
	InternalCost         float64 `json:"Internal Cost"`
	PaymentStatus        string  `json:"Payment Status"`
	PaymentInfo          string  `json:"Payment Info"`
	TelegramMessageID    string  `json:"Telegram Message ID"`
	Team                 string  `json:"Team"`
}

// *** Model for Revenue data read from RevenueDashboard ***
type RevenueEntry struct {
    Timestamp string  `json:"Timestamp"` // Read as ISO string
    Team      string  `json:"Team"`
    Page      string  `json:"Page"`
    Revenue   float64 `json:"Revenue"`
}


// *** Struct for report aggregation ***
type ReportSummary struct {
    TotalSales   float64
    TotalExpense float64 // Used in FormulaReport
}

// *** NEW: Struct for Revenue Summary ***
type RevenueAggregate struct {
    YearlyByTeam   map[int]map[string]float64    `json:"yearlyByTeam"`   // year -> team -> totalRevenue
    YearlyByPage   map[int]map[string]float64    `json:"yearlyByPage"`   // year -> page -> totalRevenue
    MonthlyByTeam  map[string]map[string]float64 `json:"monthlyByTeam"`  // "YYYY-MM" -> team -> totalRevenue
    MonthlyByPage  map[string]map[string]float64 `json:"monthlyByPage"`  // "YYYY-MM" -> page -> totalRevenue
    DailyByTeam    map[string]map[string]float64 `json:"dailyByTeam"`    // "YYYY-MM-DD" -> team -> totalRevenue
    DailyByPage    map[string]map[string]float64 `json:"dailyByPage"`    // "YYYY-MM-DD" -> page -> totalRevenue
}



// --- Apps Script Communication ---
type AppsScriptRequest struct {
	Action    string      `json:"action"`
	Secret    string      `json:"secret"`
	SheetName string      `json:"sheetName,omitempty"`
	RowData   []interface{} `json:"rowData,omitempty"` // For appendRow
	OrderId   string      `json:"orderId,omitempty"` // For findOrderRowById
	Row       int         `json:"row,omitempty"` // For update/delete
	UpdatedData map[string]interface{} `json:"updatedData,omitempty"` // For updateRow
	LogData   map[string]interface{} `json:"logData,omitempty"` // For logging
	// For image upload
	FileData string `json:"fileData,omitempty"`
	FileName string `json:"fileName,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
	// *** For overwriteSheetData ***
	Data      [][]interface{} `json:"data,omitempty"`
}

type AppsScriptResponse struct {
	Status  string      `json:"status"`
	Message string      `json:"message,omitempty"`
	Data    interface{} `json:"data,omitempty"`
	URL     string      `json:"url,omitempty"` // For image upload response
}

// ... (callAppsScriptGET remains the same) ...
func callAppsScriptGET(action string, params map[string]string) (AppsScriptResponse, error) {
	baseURL, _ := url.Parse(appsScriptURL)
	query := baseURL.Query()
	query.Set("action", action)
	query.Set("secret", appsScriptSecret)
	for key, value := range params {
		query.Set(key, value)
	}
	baseURL.RawQuery = query.Encode()

	resp, err := http.Get(baseURL.String())
	if err != nil {
		log.Printf("Error calling Apps Script GET (%s): %v", action, err)
		return AppsScriptResponse{}, fmt.Errorf("failed to connect to Google Sheets API")
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Error reading Apps Script GET response (%s): %v", action, err)
		return AppsScriptResponse{}, fmt.Errorf("failed to read Google Sheets API response")
	}

	var scriptResponse AppsScriptResponse
	err = json.Unmarshal(body, &scriptResponse)
	if err != nil {
		log.Printf("Error unmarshalling Apps Script GET response (%s): %v. Body: %s", action, err, string(body))
		return AppsScriptResponse{}, fmt.Errorf("invalid response format from Google Sheets API")
	}

	if scriptResponse.Status != "success" {
		log.Printf("Apps Script GET Error (%s): %s", action, scriptResponse.Message)
		return AppsScriptResponse{}, fmt.Errorf("Google Sheets API error: %s", scriptResponse.Message)
	}

	return scriptResponse, nil
}


// ... (callAppsScriptPOST remains the same) ...
func callAppsScriptPOST(requestData AppsScriptRequest) (AppsScriptResponse, error) {
	requestData.Secret = appsScriptSecret // Ensure secret is included
	jsonData, err := json.Marshal(requestData)
	if err != nil {
		log.Printf("Error marshalling Apps Script POST request (%s): %v", requestData.Action, err)
		return AppsScriptResponse{}, fmt.Errorf("internal error preparing data")
	}

	resp, err := http.Post(appsScriptURL, "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		log.Printf("Error calling Apps Script POST (%s): %v", requestData.Action, err)
		return AppsScriptResponse{}, fmt.Errorf("failed to connect to Google Sheets API")
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Error reading Apps Script POST response (%s): %v", requestData.Action, err)
		return AppsScriptResponse{}, fmt.Errorf("failed to read Google Sheets API response")
	}

	var scriptResponse AppsScriptResponse
	err = json.Unmarshal(body, &scriptResponse)
	if err != nil {
		log.Printf("Error unmarshalling Apps Script POST response (%s): %v. Body: %s", requestData.Action, err, string(body))
		// Try to return the raw body if unmarshalling fails
		log.Printf("Raw response body: %s", string(body))
		return AppsScriptResponse{}, fmt.Errorf("invalid response format from Google Sheets API")
	}

	// Handle specific non-200 status codes from Apps Script if needed (e.g., locked)
	if resp.StatusCode != http.StatusOK {
	    log.Printf("Apps Script POST request (%s) returned status %d. Body: %s", requestData.Action, resp.StatusCode, string(body))
	    // If it's a known error from Apps Script JSON, use that message
	    if scriptResponse.Status == "locked" {
	         return AppsScriptResponse{}, fmt.Errorf("Google Sheets API is busy, please try again")
	    }
	     if scriptResponse.Status == "error" && scriptResponse.Message != "" {
			 return AppsScriptResponse{}, fmt.Errorf("Google Sheets API error: %s", scriptResponse.Message)
		}
		// Otherwise, use a generic error based on HTTP status
		return AppsScriptResponse{}, fmt.Errorf("Google Sheets API returned status %d", resp.StatusCode)
	}

    // Even with 200 OK, check the internal status field
	if scriptResponse.Status != "success" {
		log.Printf("Apps Script POST Error (%s): %s", requestData.Action, scriptResponse.Message)
		return AppsScriptResponse{}, fmt.Errorf("Google Sheets API error: %s", scriptResponse.Message)
	}


	return scriptResponse, nil
}


// --- Fetch & Cache Sheet Data ---
func fetchSheetData(sheetName string, target interface{}) error {
	resp, err := callAppsScriptGET("getSheetData", map[string]string{"sheetName": sheetName})
	if err != nil {
		return err
	}

	// Apps Script returns data as []interface{}. We need to marshal it back to JSON
	// and then unmarshal it into our specific Go struct slice.
	jsonData, err := json.Marshal(resp.Data)
	if err != nil {
		log.Printf("Error marshalling data from Apps Script for %s: %v", sheetName, err)
		return fmt.Errorf("internal error processing sheet data")
	}

	err = json.Unmarshal(jsonData, target)
	if err != nil {
		log.Printf("Error unmarshalling data for %s: %v. JSON: %s", sheetName, err, string(jsonData))
		// Log the first few items to see the structure
		var rawData []map[string]interface{}
		_ = json.Unmarshal(jsonData, &rawData) // Ignore error here
		if len(rawData) > 0 {
			log.Printf("First item structure: %+v", rawData[0])
		}
		return fmt.Errorf("mismatched data structure for %s", sheetName)
	}

	return nil
}


func getCachedSheetData(sheetName string, target interface{}, duration time.Duration) error {
	cacheKey := "sheet_" + sheetName
	cachedData, found := getCache(cacheKey)
	if found {
		// Try to cast cached data
		jsonData, err := json.Marshal(cachedData)
		if err == nil {
			err = json.Unmarshal(jsonData, target)
			if err == nil {
				return nil // Cache hit and conversion successful
			}
			log.Printf("Error unmarshalling cached data for %s: %v", sheetName, err)
			// Proceed to fetch if cache data is invalid format
		} else {
             log.Printf("Error marshalling cached data for %s: %v", sheetName, err)
            // Proceed to fetch if cache data is invalid format
        }
	}

	// Fetch from source if not found or cache is invalid
	log.Printf("Fetching fresh data for %s", sheetName)
	err := fetchSheetData(sheetName, target)
	if err == nil {
		// Need to marshal the target back to interface{} for caching
		// This is inefficient but necessary if target is a pointer to a slice
		var dataToCache interface{}
		jsonData, _ := json.Marshal(target) // Marshal the fetched data
		_ = json.Unmarshal(jsonData, &dataToCache) // Unmarshal back into interface{}
		setCache(cacheKey, dataToCache, duration)
	}
	return err
}

// --- API Handlers ---

func handlePing(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "success", "message": "Go backend pong"})
}

func handleGetUsers(c *gin.Context) {
	var users []User
	// Use slightly longer cache for users as they change less often?
	err := getCachedSheetData("Users", &users, 15*time.Minute) // 15 min cache
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "success", "data": users})
}

func handleGetStaticData(c *gin.Context) {
	// Fetch all required static data concurrently
	var wg sync.WaitGroup
	var mu sync.Mutex // Mutex to protect concurrent writes to result map and errors
	result := make(map[string]interface{})
	errors := []string{}

	fetch := func(sheetName string, target interface{}, keyName string) {
		defer wg.Done()
		err := getCachedSheetData(sheetName, target, cacheTTL) // Use same TTL for simplicity
		mu.Lock()
		if err != nil {
			errors = append(errors, fmt.Sprintf("Failed to fetch %s: %v", keyName, err))
		} else {
			result[keyName] = target
		}
		mu.Unlock()
	}

	wg.Add(9) // Increased count for PhoneCarriers
	go fetch("TeamsPages", &[]TeamPage{}, "pages")
	go fetch("Products", &[]Product{}, "products")
	go fetch("Locations", &[]Location{}, "locations")
	go fetch("ShippingMethods", &[]ShippingMethod{}, "shippingMethods")
	go fetch("Settings", &[]map[string]interface{}{}, "settings") // Fetch settings as map for flexibility
	go fetch("Colors", &[]Color{}, "colors")
	go fetch("Drivers", &[]Driver{}, "drivers")
	go fetch("BankAccounts", &[]BankAccount{}, "bankAccounts")
    go fetch("PhoneCarriers", &[]PhoneCarrier{}, "phoneCarriers")

	wg.Wait()

	if len(errors) > 0 {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": strings.Join(errors, "; ")})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "success", "data": result})
}


// --- Placeholder for Telegram Logic ---
func sendTelegramNotification(team string, orderData map[string]interface{}) {
	log.Printf("Placeholder: Sending Telegram notification for team %s, Order ID %s", team, orderData["orderId"])
	// TODO:
	// 1. Fetch Telegram Templates for the team (can be cached - use getCachedSheetData("TelegramTemplates", ...))
    var templates []TelegramTemplate
    err := getCachedSheetData(TelegramTemplatesSheet, &templates, time.Hour) // Cache templates longer
    if err != nil {
        log.Printf("Error fetching Telegram templates for team %s: %v", team, err)
        // Optionally send a default message
        return
    }
    teamTemplates := []TelegramTemplate{}
    for _, t := range templates {
        if strings.EqualFold(t.Team, team) {
            teamTemplates = append(teamTemplates, t)
        }
    }
    // Sort by Part
    sort.Slice(teamTemplates, func(i, j int) bool {
		return teamTemplates[i].Part < teamTemplates[j].Part
	})

	// 2. Format messages using orderData and teamTemplates
	// ... (Implementation depends heavily on how placeholders work, similar to Apps Script's formatOrderForTelegram)

	// 3. Get Bot Token and Group ID for the team from config/env
	// ...

	// 4. Use a Go Telegram library to send the messages
	// ...

	// 5. Potentially send PDF (see PDF placeholder)
}
// --- Placeholder for PDF Logic ---
func generateAndSendPDF(team string, orderId string, orderData map[string]interface{}) {
    log.Printf("Placeholder: Generating/Sending PDF for team %s, Order ID %s", team, orderId)
    // TODO:
    // 1. Option A: Use a Go PDF library (e.g., gofpdf) to generate PDF directly.
    // 2. Option B: Generate HTML for the invoice (similar to Apps Script).
    //    - Then, either use a Go library to convert HTML to PDF (can be complex, might need headless Chrome)
    //    - OR send the HTML to another microservice dedicated to PDF generation.
    //    - OR simply send the HTML content somewhere if PDF isn't strictly required by Telegram bot.
    // 3. Send the generated PDF/document via Telegram.
}


func handleSubmitOrder(c *gin.Context) {
	var orderRequest struct {
		CurrentUser  User                   `json:"currentUser"`
		SelectedTeam string                 `json:"selectedTeam"`
		Page         string                 `json:"page"`
		TelegramValue string                `json:"telegramValue"`
		Customer     map[string]interface{} `json:"customer"`
		Products     []map[string]interface{} `json:"products"`
		Shipping     map[string]interface{} `json:"shipping"`
		Payment      map[string]interface{} `json:"payment"`
		Telegram     map[string]interface{} `json:"telegram"`
		Subtotal     float64                `json:"subtotal"`
		GrandTotal   float64                `json:"grandTotal"`
		Note         string                 `json:"note"`
	}

	if err := c.ShouldBindJSON(&orderRequest); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"status": "error", "message": "Invalid order data format: " + err.Error()})
		return
	}

	team := orderRequest.SelectedTeam
	if team == "" {
		c.JSON(http.StatusBadRequest, gin.H{"status": "error", "message": "Team not selected"})
		return
	}
	orderSheetName := "Orders_" + team // Assuming CONFIG.ORDER_SHEET_PREFIX

	// Prepare data row for Apps Script
	timestamp := time.Now().UTC().Format(time.RFC3339) // Use ISO format
	orderId := fmt.Sprintf("GO-%s-%d", team, time.Now().UnixNano()) // Generate ID in Go

	productsJSON, err := json.Marshal(orderRequest.Products)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to serialize products"})
		return
	}
    
    // Construct location string safely
    var locationParts []string
	if p, ok := orderRequest.Customer["province"].(string); ok && p != "" { locationParts = append(locationParts, p) }
	if d, ok := orderRequest.Customer["district"].(string); ok && d != "" { locationParts = append(locationParts, d) }
	if s, ok := orderRequest.Customer["sangkat"].(string); ok && s != "" { locationParts = append(locationParts, s) }
	fullLocation := strings.Join(locationParts, ", ")

    // Ensure numeric types from frontend are correct
    shippingFee, _ := orderRequest.Customer["shippingFee"].(float64)
    shippingCost, _ := orderRequest.Shipping["cost"].(float64)


	rowData := []interface{}{
		timestamp, orderId, orderRequest.CurrentUser.UserName, orderRequest.Page, orderRequest.TelegramValue,
		orderRequest.Customer["name"], orderRequest.Customer["phone"], fullLocation,
		orderRequest.Customer["additionalLocation"], orderRequest.Note, shippingFee,
		orderRequest.Subtotal, orderRequest.GrandTotal, string(productsJSON),
		orderRequest.Shipping["method"], orderRequest.Shipping["details"], shippingCost,
		orderRequest.Payment["status"], orderRequest.Payment["info"],
		"", // Placeholder for Telegram Message ID (Apps Script no longer handles sending)
	}

	// Append to the specific team's order sheet
	_, err = callAppsScriptPOST(AppsScriptRequest{
		Action:    "appendRow",
		SheetName: orderSheetName,
		RowData:   rowData,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to save order to Google Sheet: " + err.Error()})
		return
	}

    // Append to AllOrders sheet (optional but good for consistency)
    rowDataWithTeam := append(rowData, team)
    _, err = callAppsScriptPOST(AppsScriptRequest{
        Action: "appendRow",
        SheetName: AllOrdersSheet,
        RowData: rowDataWithTeam,
    })
    if err != nil {
        log.Printf("Warning: Failed to append to AllOrders sheet: %v", err)
        // Don't fail the whole request, just log it.
    }

	// Append to Revenue sheet
	_, err = callAppsScriptPOST(AppsScriptRequest{
		Action:    "appendRow",
		SheetName: RevenueSheet,
		RowData:   []interface{}{timestamp, team, orderRequest.Page, orderRequest.GrandTotal},
	})
	if err != nil {
		log.Printf("Warning: Failed to append to RevenueDashboard: %v", err)
	}
    
    // Log user activity via Apps Script
    _, err = callAppsScriptPOST(AppsScriptRequest{
        Action: "logUserActivity",
        SheetName: UserActivitySheet, // Specify sheet name here if needed by Apps Script
        LogData: map[string]interface{}{
            "username": orderRequest.CurrentUser.UserName,
            "action": "SUBMIT_ORDER_GO", // Indicate it came via Go backend
            "details": map[string]interface{}{"orderId": orderId, "team": team, "grandTotal": orderRequest.GrandTotal},
        },
    })
    if err != nil {
        log.Printf("Warning: Failed to log user activity for order submission: %v", err)
    }

	// --- Handle Telegram/PDF ---
	// Combine all data needed for notifications/PDF
	fullOrderData := map[string]interface{}{
	    "orderId": orderId, // Add generated Order ID
	    "currentUser": orderRequest.CurrentUser,
	    "selectedTeam": orderRequest.SelectedTeam,
	    "page": orderRequest.Page,
	    "telegramValue": orderRequest.TelegramValue,
	    "customer": orderRequest.Customer,
	    "products": orderRequest.Products,
	    "shipping": orderRequest.Shipping,
	    "payment": orderRequest.Payment,
	    "telegram": orderRequest.Telegram, // Keep scheduling info if needed
	    "subtotal": orderRequest.Subtotal,
	    "grandTotal": orderRequest.GrandTotal,
	    "note": orderRequest.Note,
	}

	// TODO: Check scheduling logic (orderRequest.Telegram)
	isScheduled, _ := orderRequest.Telegram["schedule"].(bool)
	if isScheduled {
		scheduleTimeStr, _ := orderRequest.Telegram["time"].(string)
		// TODO: Implement scheduling logic in Go (e.g., save to DB/queue, use cron)
		log.Printf("Order %s scheduled for %s (Scheduling logic TBD in Go)", orderId, scheduleTimeStr)
	} else {
		// Send notifications immediately (placeholders)
		go sendTelegramNotification(team, fullOrderData)
		go generateAndSendPDF(team, orderId, fullOrderData)
	}

	// Invalidate relevant caches
	invalidateSheetCache(AllOrdersSheet)
	invalidateSheetCache(RevenueSheet)
	invalidateSheetCache(orderSheetName) // Invalidate specific team sheet


	c.JSON(http.StatusOK, gin.H{"status": "success", "orderId": orderId})
}

// --- Handler for Image Upload Proxy ---
func handleImageUploadProxy(c *gin.Context) {
    var uploadRequest struct {
		Action    string      `json:"action"` // Should be "uploadImage"
		FileData string `json:"fileData"`
		FileName string `json:"fileName"`
		MimeType string `json:"mimeType"`
		// Pass through sheet/pk/column info needed by Apps Script for cell update
		SheetName string `json:"sheetName"`
		PrimaryKey map[string]string `json:"primaryKey"`
		ColumnName string `json:"columnName"`
		// Admin user for logging (optional, could be added by Go backend based on session)
		// AdminUser string `json:"adminUser"`
	}

	if err := c.ShouldBindJSON(&uploadRequest); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"status": "error", "message": "Invalid image upload data format: " + err.Error()})
		return
	}

	// Call the Apps Script upload function to get the URL
	resp, err := callAppsScriptPOST(AppsScriptRequest{
		Action:    "uploadImage", // Action for Apps Script
		FileData: uploadRequest.FileData,
		FileName: uploadRequest.FileName,
		MimeType: uploadRequest.MimeType,
        // Log activity from Go backend or pass user info if needed
		// LogData: map[string]interface{}{"username": "GoBackendProxy", "action": "PROXY_IMAGE_UPLOAD", "details": ...},
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to upload image via Google API: " + err.Error()})
		return
	}

    // --- Update the specific sheet cell after upload ---
    if uploadRequest.SheetName != "" && uploadRequest.PrimaryKey != nil && uploadRequest.ColumnName != "" && resp.URL != "" {
        go func() { // Update sheet in the background
            pkHeader := ""
            pkValue := ""
            for k, v := range uploadRequest.PrimaryKey {
                pkHeader = k
                pkValue = v
                break // Assuming single primary key
            }
            if pkHeader == "" || pkValue == "" {
                log.Printf("Warning: Missing primary key info for image update. Sheet: %s, Column: %s", uploadRequest.SheetName, uploadRequest.ColumnName)
                return
            }

            // Fetch current sheet data (use cache if available, but might be slightly stale)
            var sheetRows []map[string]interface{}
            targetRowIndex := -1

            // Prioritize cache for finding row quickly
            cacheKey := "sheet_" + uploadRequest.SheetName
            cachedData, found := getCache(cacheKey)
            if found {
                 jsonData, err := json.Marshal(cachedData)
                 if err == nil {
                     _ = json.Unmarshal(jsonData, &sheetRows) // Ignore error, try fetching if fail
                 }
            }
            
            // If cache missed or was invalid format, fetch fresh
            if len(sheetRows) == 0 {
                 err := fetchSheetData(uploadRequest.SheetName, &sheetRows)
                 if err != nil {
                     log.Printf("Error fetching sheet %s to update image URL: %v", uploadRequest.SheetName, err)
                     return
                 }
            }


            // Find the row index
            for i, row := range sheetRows {
                 if val, ok := row[pkHeader]; ok && fmt.Sprintf("%v", val) == pkValue {
                      targetRowIndex = i + 2 // +2 for 1-based index and header row
                      break
                 }
            }

            if targetRowIndex == -1 {
                 log.Printf("Warning: Row not found for PK %s=%s in sheet %s for image update.", pkHeader, pkValue, uploadRequest.SheetName)
                 return
            }

            // Call updateRow in Apps Script
            updatePayload := AppsScriptRequest{
                Action: "updateRow",
                SheetName: uploadRequest.SheetName,
                Row: targetRowIndex,
                UpdatedData: map[string]interface{}{
                    uploadRequest.ColumnName: resp.URL,
                },
            }
             _, updateErr := callAppsScriptPOST(updatePayload)
             if updateErr != nil {
                  log.Printf("Error updating sheet %s row %d with image URL: %v", uploadRequest.SheetName, targetRowIndex, updateErr)
             } else {
                  log.Printf("Successfully updated sheet %s row %d with image URL for column %s", uploadRequest.SheetName, targetRowIndex, uploadRequest.ColumnName)
                  invalidateSheetCache(uploadRequest.SheetName) // Invalidate cache
             }
        }() // End background update
    }

	c.JSON(http.StatusOK, gin.H{"status": "success", "url": resp.URL})
}


// --- Handler for Formula Report Update ---
func handleUpdateFormulaReport(c *gin.Context) {
	// 1. Fetch AllOrders data
	var allOrders []Order
	// Use a longer timeout cache for reports? Or maybe no cache to ensure freshness? Let's use standard cache for now.
	err := getCachedSheetData(AllOrdersSheet, &allOrders, cacheTTL)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to fetch order data: " + err.Error()})
		return
	}

	if len(allOrders) == 0 {
		// Overwrite with headers only if no data
		reportData := [][]interface{}{
			{"Category", "Period", "Total Sales", "Total Expense"},
		}
		_, err = callAppsScriptPOST(AppsScriptRequest{
			Action:    "overwriteSheetData",
			SheetName: FormulaReportSheet,
			Data:      reportData,
		})
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to clear/write headers to report sheet: " + err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "success", "message": "Formula Report updated (No order data found)."})
		return
	}

	// 2. Process Data in Go
	yearlyData := make(map[int]*ReportSummary)
	monthlyData := make(map[string]*ReportSummary) // Key: "YYYY-MM"
	dailyData := make(map[string]*ReportSummary)   // Key: "YYYY-MM-DD"

	now := time.Now()
	currentYear := now.Year()
	currentMonth := now.Month()
    loc, _ := time.LoadLocation("Asia/Phnom_Penh") // Load Cambodia timezone


	for _, order := range allOrders {
		// Try parsing different potential timestamp formats from Apps Script
		ts, err := time.Parse(time.RFC3339, order.Timestamp) // Try ISO string first
        if err != nil {
             // Try common Apps Script Date object string format (might vary)
             // Example: "Sat Oct 26 2024 14:30:00 GMT+0700 (Indochina Time)" - Needs specific layout
             // Let's assume RFC3339 works for now based on the getSheetData modification
             log.Printf("Warning: Could not parse timestamp '%s' for order %s: %v. Skipping record.", order.Timestamp, order.OrderID, err)
             continue
        }
        ts = ts.In(loc) // Convert to local time for aggregation


		year := ts.Year()
		month := ts.Month()
		day := ts.Day()
		yearMonthKey := fmt.Sprintf("%d-%02d", year, month)
		yearMonthDayKey := fmt.Sprintf("%d-%02d-%02d", year, month, day)

		// Aggregate Yearly
		if _, ok := yearlyData[year]; !ok {
			yearlyData[year] = &ReportSummary{}
		}
		yearlyData[year].TotalSales += order.GrandTotal
		yearlyData[year].TotalExpense += order.InternalCost

		// Aggregate Monthly (Current Year)
		if year == currentYear {
			if _, ok := monthlyData[yearMonthKey]; !ok {
				monthlyData[yearMonthKey] = &ReportSummary{}
			}
			monthlyData[yearMonthKey].TotalSales += order.GrandTotal
			monthlyData[yearMonthKey].TotalExpense += order.InternalCost
		}

		// Aggregate Daily (Current Month of Current Year)
		if year == currentYear && month == currentMonth {
			if _, ok := dailyData[yearMonthDayKey]; !ok {
				dailyData[yearMonthDayKey] = &ReportSummary{}
			}
			dailyData[yearMonthDayKey].TotalSales += order.GrandTotal
			dailyData[yearMonthDayKey].TotalExpense += order.InternalCost
		}
	}

	// 3. Format Output for Sheet
	reportData := [][]interface{}{
		{"Category", "Period", "Total Sales", "Total Expense"}, // Header row
	}

	// Add Yearly Data
	reportData = append(reportData, []interface{}{"YEARLY REPORT", "", "", ""})
	years := make([]int, 0, len(yearlyData))
	for y := range yearlyData {
		years = append(years, y)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(years))) // Sort years descending
	for _, year := range years {
		summary := yearlyData[year]
		reportData = append(reportData, []interface{}{"", year, fmt.Sprintf("%.2f", summary.TotalSales), fmt.Sprintf("%.2f", summary.TotalExpense)})
	}
	reportData = append(reportData, []interface{}{}) // Blank row

	// Add Monthly Data (Current Year)
	reportData = append(reportData, []interface{}{fmt.Sprintf("MONTHLY REPORT (%d)", currentYear), "", "", ""})
	// monthKeys := make([]string, 0, 12) // No need to pre-collect keys
    // for m := 1; m <= 12; m++ {
    //     monthKey := fmt.Sprintf("%d-%02d", currentYear, m)
    //     // Check if data exists for this month before adding the key
    //     if _, ok := monthlyData[monthKey]; ok {
    //         monthKeys = append(monthKeys, monthKey)
    //     }
    // }
    // No need to sort monthKeys if generated sequentially 1-12
	// sort.Strings(monthKeys) // Sort months chronologically
	for m := 1; m <= 12; m++ {
        monthKey := fmt.Sprintf("%d-%02d", currentYear, m)
        summary, ok := monthlyData[monthKey]
        monthName := time.Month(m).String() // Get English month name
		if ok {
		     reportData = append(reportData, []interface{}{"", monthName, fmt.Sprintf("%.2f", summary.TotalSales), fmt.Sprintf("%.2f", summary.TotalExpense)})
		} else {
             // Optionally show months with zero values
             // reportData = append(reportData, []interface{}{"", monthName, "0.00", "0.00"})
        }
    }

	reportData = append(reportData, []interface{}{}) // Blank row

	// Add Daily Data (Current Month)
	reportData = append(reportData, []interface{}{fmt.Sprintf("DAILY REPORT (%s %d)", currentMonth.String(), currentYear), "", "", ""})
	dayKeys := make([]string, 0, len(dailyData))
	for d := range dailyData {
		dayKeys = append(dayKeys, d)
	}
	sort.Strings(dayKeys) // Sort days chronologically
	for _, dayKey := range dayKeys {
		summary := dailyData[dayKey]
        // Format date like "Oct 28, 2025"
        t, _ := time.Parse("2006-01-02", dayKey)
		dayLabel := t.Format("Jan 02, 2006")
		reportData = append(reportData, []interface{}{"", dayLabel, fmt.Sprintf("%.2f", summary.TotalSales), fmt.Sprintf("%.2f", summary.TotalExpense)})
	}

	// 4. Write Data to Sheet via Apps Script
	_, err = callAppsScriptPOST(AppsScriptRequest{
		Action:    "overwriteSheetData",
		SheetName: FormulaReportSheet,
		Data:      reportData,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to write report data: " + err.Error()})
		return
	}
    
    // Invalidate AllOrders cache? Maybe not necessary just for report generation.

	c.JSON(http.StatusOK, gin.H{"status": "success", "message": "Formula Report updated successfully."})
}


// --- *** NEW: Handler for Revenue Summary Report *** ---
func handleGetRevenueSummary(c *gin.Context) {
	// 1. Fetch RevenueDashboard data
	var revenueEntries []RevenueEntry
	// Use cache
	err := getCachedSheetData(RevenueSheet, &revenueEntries, cacheTTL)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"status": "error", "message": "Failed to fetch revenue data: " + err.Error()})
		return
	}

	if len(revenueEntries) == 0 {
		c.JSON(http.StatusOK, gin.H{"status": "success", "data": RevenueAggregate{ // Return empty structure
		    YearlyByTeam:   make(map[int]map[string]float64),
            YearlyByPage:   make(map[int]map[string]float64),
            MonthlyByTeam:  make(map[string]map[string]float64),
            MonthlyByPage:  make(map[string]map[string]float64),
            DailyByTeam:    make(map[string]map[string]float64),
            DailyByPage:    make(map[string]map[string]float64),
		}})
		return
	}

	// 2. Process Data in Go
	yearlyByTeam := make(map[int]map[string]float64)
    yearlyByPage := make(map[int]map[string]float64)
    monthlyByTeam := make(map[string]map[string]float64) // Key: "YYYY-MM"
    monthlyByPage := make(map[string]map[string]float64) // Key: "YYYY-MM"
    dailyByTeam := make(map[string]map[string]float64)   // Key: "YYYY-MM-DD"
    dailyByPage := make(map[string]map[string]float64)   // Key: "YYYY-MM-DD"

	now := time.Now()
	currentYear := now.Year()
	currentMonth := now.Month()
    loc, _ := time.LoadLocation("Asia/Phnom_Penh") // Load Cambodia timezone


	for _, entry := range revenueEntries {
		ts, err := time.Parse(time.RFC3339, entry.Timestamp)
        if err != nil {
             log.Printf("Warning: Could not parse timestamp '%s' for revenue entry. Skipping.", entry.Timestamp)
             continue
        }
        ts = ts.In(loc) // Convert to local time

		year := ts.Year()
		month := ts.Month()
		// day := ts.Day() // Not needed for key directly
		yearMonthKey := fmt.Sprintf("%d-%02d", year, month)
		yearMonthDayKey := ts.Format("2006-01-02") // Use standard format for daily key

        team := entry.Team
        page := entry.Page
        revenue := entry.Revenue

        // --- Aggregate Yearly ---
        if _, ok := yearlyByTeam[year]; !ok { yearlyByTeam[year] = make(map[string]float64) }
        yearlyByTeam[year][team] += revenue

        if _, ok := yearlyByPage[year]; !ok { yearlyByPage[year] = make(map[string]float64) }
        yearlyByPage[year][page] += revenue

        // --- Aggregate Monthly (Current Year Only) ---
		if year == currentYear {
			if _, ok := monthlyByTeam[yearMonthKey]; !ok { monthlyByTeam[yearMonthKey] = make(map[string]float64) }
            monthlyByTeam[yearMonthKey][team] += revenue

            if _, ok := monthlyByPage[yearMonthKey]; !ok { monthlyByPage[yearMonthKey] = make(map[string]float64) }
            monthlyByPage[yearMonthKey][page] += revenue
		}

		// --- Aggregate Daily (Current Month of Current Year Only) ---
		if year == currentYear && month == currentMonth {
			if _, ok := dailyByTeam[yearMonthDayKey]; !ok { dailyByTeam[yearMonthDayKey] = make(map[string]float64) }
            dailyByTeam[yearMonthDayKey][team] += revenue

            if _, ok := dailyByPage[yearMonthDayKey]; !ok { dailyByPage[yearMonthDayKey] = make(map[string]float64) }
            dailyByPage[yearMonthDayKey][page] += revenue
		}
	}

    // 3. Prepare response
    response := RevenueAggregate{
        YearlyByTeam:   yearlyByTeam,
        YearlyByPage:   yearlyByPage,
        MonthlyByTeam:  monthlyByTeam,
        MonthlyByPage:  monthlyByPage,
        DailyByTeam:    dailyByTeam,
        DailyByPage:    dailyByPage,
    }

	c.JSON(http.StatusOK, gin.H{"status": "success", "data": response})
}


// --- Main Function ---
func main() {
	// Load configuration from environment variables
	appsScriptURL = os.Getenv("APPS_SCRIPT_URL")
	appsScriptSecret = os.Getenv("APPS_SCRIPT_SECRET")
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080" // Default port for local development
	}
	renderBaseURL = os.Getenv("RENDER_EXTERNAL_URL") // Render provides this automatically

	if appsScriptURL == "" || appsScriptSecret == "" {
		log.Fatal("APPS_SCRIPT_URL and APPS_SCRIPT_SECRET environment variables are required.")
	}
	log.Printf("Connecting to Apps Script URL: %s", appsScriptURL)
	log.Printf("Render Base URL: %s", renderBaseURL)


	// --- Setup Gin Router ---
	router := gin.Default()

	// CORS configuration (allow requests from your frontend domain)
	config := cors.DefaultConfig()
    // Allow all origins for simplicity during development, restrict in production
	config.AllowOrigins = []string{"*"}
	config.AllowMethods = []string{"GET", "POST", "OPTIONS"}
	config.AllowHeaders = []string{"Origin", "Content-Type", "Accept"}
	router.Use(cors.New(config))


	// --- Define API Routes ---
	api := router.Group("/api") // Group API routes under /api
	{
		api.GET("/ping", handlePing)
		api.GET("/users", handleGetUsers) // Corresponds to ?action=getUsers
		api.GET("/static-data", handleGetStaticData) // Corresponds to ?action=getStaticData
		// Add GET handlers for other data types if needed (e.g., /api/products, /api/locations)

		api.POST("/submit-order", handleSubmitOrder) // Corresponds to { action: 'submitOrder', ... }
        api.POST("/upload-image", handleImageUploadProxy) // Proxy for image uploads

        // *** Admin Endpoints ***
        admin := api.Group("/admin")
        // TODO: Add authentication middleware for admin routes
        {
             admin.POST("/update-formula-report", handleUpdateFormulaReport)
             admin.GET("/revenue-summary", handleGetRevenueSummary) // *** NEW ***
            // TODO: Add other admin endpoints here
            // admin.GET("/all-orders", handleGetAllOrders)
            // admin.POST("/update-order", handleAdminUpdateOrder)
            // admin.POST("/delete-order", handleAdminDeleteOrder)
            // admin.GET("/reports", handleGetReportData) // Endpoint for the specific report view in Index.html?
            // admin.POST("/update-sheet", handleAdminUpdateSheet)
            // admin.POST("/delete-row", handleAdminDeleteRow)
        }

		// TODO: Add POST handlers for:
		// - /login (Implement authentication)
		// - /update-profile
		// - writeLog (maybe combine logging within Go handlers)
	}

	// --- Serve Frontend (Optional, if hosted together) ---
	// router.StaticFS("/", http.Dir("./frontend")) // Assuming frontend files are in ./frontend
    // router.NoRoute(func(c *gin.Context) {
	// 	c.File("./frontend/index.html")
	// })


	// --- Start Server ---
	log.Printf("Starting Go backend server on port %s", port)
	err := router.Run(":" + port)
	if err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}

